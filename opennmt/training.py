"""Training related classes and functions."""

import collections
import time
import six

import tensorflow as tf

from opennmt.data import dataset as dataset_util


class Trainer(object):
  """Model trainer."""

  def __init__(self, checkpoint, devices=None):
    """Initializes the trainer.

    Args:
      checkpoint: A :class:`opennmt.utils.checkpoint.Checkpoint` instance.
      devices: List of device strings to use for training.
    """
    if checkpoint.optimizer is None:
      raise ValueError("No optimizer is defined")
    if not devices:
      devices = tf.config.experimental.list_logical_devices(device_type="GPU")
      if not devices:
        devices = tf.config.experimental.list_logical_devices(device_type="CPU")
      devices = [devices[0].name]
    self._checkpoint = checkpoint
    self._model = checkpoint.model
    self._optimizer = checkpoint.optimizer
    self._strategy = tf.distribute.MirroredStrategy(devices=devices)
    self._summary_writer = tf.summary.create_file_writer(checkpoint.model_dir)

  def __call__(self,
               dataset,
               max_step=None,
               accum_steps=1,
               report_steps=100,
               save_steps=5000,
               evaluator=None,
               eval_steps=5000):
    """Runs the training.

    Args:
      dataset: A training dataset.
      max_step: The final training step.
      accum_steps: The number of gradient accumulation steps.
      report_steps: Report status every this many steps.
      save_steps: Save a checkpoint every this many steps.
      evaluator: A :class:`opennmt.evaluation.Evaluator` instance to call for
        evaluation.
      eval_steps: Evaluate every this many steps.
    """
    if max_step is not None and self._optimizer.iterations.numpy() >= max_step:
      tf.get_logger().warning("Model already reached train_steps = %d. Exiting.", max_step)
      return

    with self._strategy.scope():
      self._model.create_variables(optimizer=self._optimizer)
      dataset = self._strategy.experimental_distribute_dataset(dataset)

    variables = self._model.variables
    gradients = []
    for variable in variables:
      gradients.append(tf.Variable(tf.zeros_like(variable), trainable=False))

    def _accumulate_gradients(source, target):
      outputs, _ = self._model(
          source,
          labels=target,
          step=self._optimizer.iterations,
          mode=tf.estimator.ModeKeys.TRAIN)
      loss = self._model.compute_loss(outputs, target, training=True)
      loss = loss[0] / loss[1]
      step_gradients = self._model.compute_gradients(loss, self._optimizer, variables=variables)
      for gradient, step_gradient in zip(gradients, step_gradients):
        gradient.assign_add(step_gradient)
      num_words = {}
      if "length" in source:
        num_words["source"] = tf.reduce_sum(source["length"])
      if "length" in target:
        num_words["target"] = tf.reduce_sum(target["length"])
      return loss, num_words

    def _apply_gradients():
      self._optimizer.apply_gradients(list(zip(gradients, variables)))
      for gradient in gradients:
        gradient.assign(tf.zeros_like(gradient))

    @dataset_util.function_on_next(dataset)
    def _forward(next_fn):
      with self._strategy.scope():
        per_replica_source, per_replica_target = next_fn()
        per_replica_loss, per_replica_words = self._strategy.experimental_run_v2(
            _accumulate_gradients, args=(per_replica_source, per_replica_target))
        loss = self._strategy.reduce(tf.distribute.ReduceOp.MEAN, per_replica_loss, None)
        num_words = {
            k:self._strategy.reduce(tf.distribute.ReduceOp.SUM, v, None)
            for k, v in six.iteritems(per_replica_words)}
      return loss, num_words

    @tf.function
    def _step():
      with self._strategy.scope():
        self._strategy.experimental_run_v2(_apply_gradients)

    accum_num_words = collections.defaultdict(int)
    last_report_time = time.time()
    last_step = 0

    with self._summary_writer.as_default():
      for i, (loss, num_words) in enumerate(_forward()):
        if i == 0 or (i + 1) % accum_steps == 0:
          _step()

        for key, value in six.iteritems(num_words):
          accum_num_words[key] += value.numpy()
        step = self._optimizer.iterations.numpy()
        if step == last_step:
          continue  # Do not process same step twice.
        last_step = step
        if step % report_steps == 0:
          last_report_time = _report_training_status(
              step,
              loss,
              self._optimizer.learning_rate,
              accum_num_words,
              last_report_time)
        if save_steps is not None and step % save_steps == 0:
          self._checkpoint.save(step)
        if evaluator is not None and eval_steps is not None and step % eval_steps == 0:
          evaluator(step)
        if step == max_step:
          break

    self._checkpoint.save(step)


def _report_training_status(step, loss, learning_rate, accum_num_words, last_report_time):
  tf.summary.experimental.set_step(step)
  new_report_time = time.time()
  words_per_sec_fmt = []
  for key, value in six.iteritems(accum_num_words):
    avg = int(value / (new_report_time - last_report_time))
    accum_num_words[key] = 0
    tf.summary.scalar(
        "words_per_sec/%s" % key,
        avg,
        description="%s words per second" % key.capitalize())
    fmt = "%s words/s = %d" % (key, avg)
    words_per_sec_fmt.append(fmt)
  words_per_sec_fmt = sorted(words_per_sec_fmt)
  if isinstance(learning_rate, tf.optimizers.schedules.LearningRateSchedule):
    learning_rate = learning_rate(step)
  tf.get_logger().info(
      "Step = %d ; %s ; Learning rate = %f ; Loss = %f",
      step,
      ", ".join(words_per_sec_fmt),
      learning_rate,
      loss)
  tf.summary.scalar("loss", loss, description="Training loss")
  tf.summary.scalar("optim/learning_rate", learning_rate, description="Learning rate")
  return new_report_time
