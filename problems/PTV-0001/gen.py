#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gen.py —— 数据生成器

用法:
    python gen.py --seed 1 --mode small
    python gen.py --seed 2 --mode large
    python gen.py --seed 3 --mode edge
"""
import argparse
import random


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mode", choices=["small", "large", "edge"], default="small")
    args = ap.parse_args()
    random.seed(args.seed)

    if args.mode == "small":
        n = random.randint(1, 10)
        a = [random.randint(-100, 100) for _ in range(n)]
    elif args.mode == "large":
        n = random.randint(90000, 100000)
        a = [random.randint(-10**9, 10**9) for _ in range(n)]
    else:  # edge
        n = random.choice([1, 2])
        a = [random.choice([-10**9, 0, 10**9]) for _ in range(n)]

    print(n)
    print(" ".join(map(str, a)))


if __name__ == "__main__":
    main()
