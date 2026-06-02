"""Multi-display exhibit UI package.

A single Qt process drives four windows:
  - One large portrait top display (video + topic + gaze guidance)
  - Three mini displays, one under each robot (subtitles + idle fade + gaze)

The old single-window ControlledVideoWindow path is left untouched; this
package is an additive new mode. See displays/manager.py for the entry point.
"""
