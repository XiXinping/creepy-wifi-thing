"""Warp a reference photo onto the board's top plane: a true top-down view on a 1 mm grid, to measure parts from.

Run from site/: tools/.venv/bin/python tools/blender/rectify.py photo.jpg out.png
The homography comes from the castellated pins' through-holes (pitch 2.54 mm, |y| = PIN_HOLE_Y), clicked in
xiao-c5-top.jpg below. Only the board top (z = 1 mm) is exact; a feature h mm higher lands about
h * (0.6, 0.85) mm further from the camera (measured off the 1 mm board edge), which is how the shield can's
height was read. Output frame: x -13..13 mm left to right, y +10..-10 top to bottom, USB-C to the right.
"""
import sys

import cv2
import numpy as np

PX = 50  # px per mm
PIN_HOLE_Y = 7.47
xs = [-8.437 + 2.54 * k for k in range(7)]
# hole centres in reference/xiao-c5-top.jpg: the near row (y < 0, k = 6..0) and the two far holes not under the can
near = [(1030, 502), (973.3, 543.3), (923.3, 586.7), (868.3, 628.3), (813.3, 673.3), (758.3, 715), (705, 758)]
far = [(675, 276.7), (348.3, 526.7)]
board = [(xs[6 - i], -PIN_HOLE_Y) for i in range(7)] + [(xs[6], PIN_HOLE_Y), (xs[0], PIN_HOLE_Y)]

im = cv2.imread(sys.argv[1])
to_px = lambda x, y: ((x + 13) * PX, (10 - y) * PX)
H, _ = cv2.findHomography(np.array(near + far, float), np.array([to_px(*b) for b in board], float))
out = cv2.warpPerspective(im, H, (26 * PX, 20 * PX), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
for x in range(-13, 14):
    X = int((x + 13) * PX)
    cv2.line(out, (X, 0), (X, 20 * PX), (255, 200, 0) if x % 5 else (255, 0, 255), 1)
for y in range(-10, 11):
    Y = int((10 - y) * PX)
    cv2.line(out, (0, Y), (26 * PX, Y), (255, 200, 0) if y % 5 else (255, 0, 255), 1)
cv2.imwrite(sys.argv[2], out)
