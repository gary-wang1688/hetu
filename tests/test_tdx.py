"""
mootdx 连通性测试 — 通达信 TCP 行情

用法: python tests/test_tdx.py
"""

import sys
import time


def test():
    try:
        from mootdx.quotes import Quotes
    except ImportError:
        print("❌ mootdx 未安装, pip install mootdx")
        return 1

    client = Quotes.factory(market='std')

    # 1. 单只
    print("📊 单只行情...")
    try:
        df = client.quotes(['000001.SZ'])
        r = df.iloc[0]
        print(f"   平安银行: price={r['price']:.2f} open={r['open']:.2f} "
              f"pre_close={r['last_close']:.2f} vol={r['vol']}")
        print("   ✅")
    except Exception as e:
        print(f"   ❌ {e}")
        client.close()
        return 1

    # 2. 批量
    print("\n📊 批量 (10 只)...")
    stocks = ['000001.SZ', '000002.SZ', '600519.SH', '600036.SH',
              '002138.SZ', '300285.SZ', '300408.SZ', '600699.SH',
              '600900.SH', '601318.SH']
    try:
        t0 = time.time()
        df = client.quotes(stocks)
        ms = (time.time() - t0) * 1000
        print(f"   耗时: {ms:.0f}ms, 返回 {len(df)} 条")
        for _, r in df.iterrows():
            p = r['price']
            pc = r['last_close']
            chg = (p - pc) / pc * 100 if pc else 0
            print(f"   {r['code']:12s}  {p:10.2f}  {chg:+.2f}%")
        print(f"   ✅ 批量 ({ms:.0f}ms)")
    except Exception as e:
        print(f"   ❌ {e}")
        client.close()
        return 1

    # 3. 持仓标的
    print("\n📊 当前持仓...")
    held = ['002138.SZ', '300285.SZ', '300408.SZ', '600699.SH', '600900.SH']
    try:
        t0 = time.time()
        df = client.quotes(held)
        ms = (time.time() - t0) * 1000
        for _, r in df.iterrows():
            p = r['price']
            pc = r['last_close']
            chg = (p - pc) / pc * 100 if pc else 0
            print(f"   {r['code']:12s}  {p:10.2f}  {chg:+.2f}%  vol={r['vol']}")
        print(f"   ✅ ({ms:.0f}ms)")
    except Exception as e:
        print(f"   ❌ {e}")

    client.close()
    print("\n✅ mootdx 可用")
    return 0


if __name__ == "__main__":
    sys.exit(test())
