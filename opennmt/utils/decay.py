"""Define learning rate decay functions."""

import tensorflow as tf
import numpy as np


class NoamDecay(tf.optimizers.schedules.LearningRateSchedule):
  """Defines the decay function described in https://arxiv.org/abs/1706.03762."""

  def __init__(self, scale, model_dim, warmup_steps):
    """Initializes the decay function.

    Args:
      scale: The scale constant.
      model_dim: The model dimension.
      warmup_steps: The number of warmup steps.
    """
    self.scale = tf.cast(scale, tf.float32)
    self.model_dim = tf.cast(model_dim, tf.float32)
    self.warmup_steps = tf.cast(warmup_steps, tf.float32)

  def __call__(self, step):
    step = tf.cast(step + 1, tf.float32)
    return (self.scale
            * tf.pow(self.model_dim, -0.5)
            * tf.minimum(tf.pow(step, -0.5), step * tf.pow(self.warmup_steps, -1.5)))


class RsqrtDecay(tf.optimizers.schedules.LearningRateSchedule):
  """Decay based on the reciprocal of the step square root."""

  def __init__(self, scale, warmup_steps):
    """Initializes the decay function.

    Args:
      scale: The scale constant.
      warmup_steps: The number of warmup steps.
    """
    self.scale = tf.cast(scale, tf.float32)
    self.warmup_steps = tf.cast(warmup_steps, tf.float32)

  def __call__(self, step):
    step = tf.cast(step, tf.float32)
    return self.scale * tf.rsqrt(tf.maximum(step, self.warmup_steps))


class CosineAnnealing(tf.optimizers.schedules.LearningRateSchedule):
  """Decay using a cosine annealing schedule."""

  def __init__(self, eta_max, eta_min=0, max_step=1000000, warmup_steps=None):
    """Initializes the decay function.

    Args:
      eta_max: Maximum learning rate.
      eta_min: Minimum learning rate.
      max_step: The last step of the scedule.
      warmup_steps: The number of steps to increment the learning rate linearly
        from 0 to :obj:`scale` before annealing.
    """
    self.eta_max = tf.cast(eta_max, tf.float32)
    self.eta_min = tf.cast(eta_min, tf.float32)
    self.max_step = tf.cast(max_step, tf.float32)
    self.warmup_steps = tf.cast(warmup_steps, tf.float32) if warmup_steps is not None else None

  def __call__(self, step):
    step = tf.cast(step, tf.float32)
    annealing = lambda: (
        self.eta_min
        + 0.5 * (self.eta_max - self.eta_min) * (1 + tf.cos(np.pi * step / self.max_step)))
    linear = lambda: self.eta_max * step / tf.cast(self.warmup_steps, tf.float32)
    if self.warmup_steps is None:
      return annealing()
    return tf.cond(tf.less(step, self.warmup_steps), true_fn=linear, false_fn=annealing)


class RNMTPlusDecay(tf.optimizers.schedules.LearningRateSchedule):
  """Defines the decay function described in https://arxiv.org/abs/1804.09849."""

  def __init__(self,
               scale,
               num_replicas,
               warmup_steps=500,
               start_step=600000,
               end_step=1200000):
    """Initializes the decay function.

    Args:
      scale: The scale constant.
      num_replicas: The number of concurrent model replicas.
      warmup_steps: The number of warmup steps.
      start_step: The start step of the exponential decay.
      end_step: The end step of the exponential decay.
    """
    self.scale = tf.cast(scale, tf.float32)
    self.num_replicas = tf.cast(num_replicas, tf.float32)
    self.warmup_steps = tf.cast(warmup_steps, tf.float32)
    self.start_step = tf.cast(start_step, tf.float32)
    self.end_step = tf.cast(end_step, tf.float32)

  def __call__(self, step):
    t = tf.cast(step, tf.float32)
    n = self.num_replicas
    p = self.warmup_steps
    s = self.start_step
    e = self.end_step
    return self.scale * tf.minimum(
        tf.minimum(1 + (t * (n - 1)) / (n * p), n),
        n * tf.pow(2 * n, (s - n * t) / (e - s)))
