#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STEERING ZEROING FROM THE LEFT (measured by hand).
Because of the servo's dead band + stiction, coming FROM THE RIGHT it does not reach zero
(it settles tilted to the right), but FROM THE LEFT it settles exactly.
RECIPE: wherever the steering was -> go to -25, then ONE big jump to 0.
Usage: python3 zero_steer.py  (or import zero_steer; zero_steer.zero(px))"""
import time

def zero(px):
    px.set_dir_servo_angle(-25)
    time.sleep(0.6)
    px.set_dir_servo_angle(0)
    time.sleep(0.3)

if __name__ == '__main__':
    from picarx import Picarx
    p = Picarx()
    time.sleep(0.5)
    zero(p)
    print('steering zeroed (approached from the left)')
