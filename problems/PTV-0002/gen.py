#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gen.py —— 数据生成器（每组一行 n）"""
import argparse
import random


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mode", choices=["small", "large", "edge"], default="small")
    args = ap.parse_args()
    random.seed(args.seed)

    if args.mode == "small":
        t = random.randint(1, 5)
    elif args.mode == "large":
        t = random.randint(900, 1000)
    else:
        t = random.choice([1, 2])

    print(t)
    for _ in range(t):
        if args.mode == "large":
            print(random.randint(1, 20))
        elif args.mode == "edge":
            print(random.choice([1, 20]))
        else:
            print(random.randint(1, 12))


if __name__ == "__main__":
    main()
