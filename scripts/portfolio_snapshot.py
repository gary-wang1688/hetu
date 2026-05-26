"""
河图 (HeTu) 盘中持仓快报 — MX 模拟盘 + DeepSeek 总结 + 企业微信推送

用法: python scripts/portfolio_snapshot.py
"""
from __future__ import annotations
import asyncio, json, os, sys, yaml
from datetime import datetime
import aiohttp

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


async def mx_call(session, base, key, endpoint, payload):
    headers = {"apikey": key}
    async with session.post(f"{base}{endpoint}", json=payload, headers=headers) as r:
        return await r.json()


async def main():
    cfg_path = os.path.join(PROJECT_ROOT, "config", "default.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    mx_key = cfg["brokers"]["mx"]["api_key"]
    mx_base = cfg["brokers"]["mx"]["base_url"]
    llm_key = cfg["llm"]["api_key"]
    webhook = cfg["notifications"]["channels"]["wecom"]["webhook_url"]

    async with aiohttp.ClientSession() as s:
        # 1. 余额
        print("📊 查询 MX 模拟盘...")
        bal_data = await mx_call(s, mx_base, mx_key, "/api/claw/mockTrading/balance", {"moneyUnit": 1})
        if str(bal_data.get("code", "")) != "200":
            print(f"❌ 余额查询失败: {bal_data.get('message')}")
            return
        inner = bal_data["data"]
        balance = {
            "total_assets": float(inner["totalAssets"]),
            "avail_balance": float(inner["availBalance"]),
            "total_pos_value": inner["totalAssets"] - inner["availBalance"],
            "nav": float(inner["nav"]),
            "account_name": inner["accName"],
        }
        print(f"   账户: {balance['account_name']}, 总资产 ¥{balance['total_assets']:,.2f}")

        # 2. 持仓
        pos_data = await mx_call(s, mx_base, mx_key, "/api/claw/mockTrading/positions", {"moneyUnit": 1})
        pos_list = (pos_data.get("data", {}) or {}).get("posList") or []
        print(f"   持仓: {len(pos_list)} 只")

        positions = []
        codes_for_tdx = []
        for raw in pos_list:
            sec_mkt = raw.get("secMkt", 0)
            mx_code = str(raw.get("secCode", ""))
            suffix = ".SH" if sec_mkt == 1 else ".SZ"
            code = f"{mx_code}{suffix}"
            price_dec = raw.get("priceDec", 2)
            cost_dec = raw.get("costPriceDec", 2)
            shares = raw.get("count", 0)
            cost = float(raw.get("costPrice", 0)) / (10 ** cost_dec)
            price = float(raw.get("price", 0)) / (10 ** price_dec)
            pnl = (price - cost) * shares

            positions.append({
                "code": code, "mx_code": mx_code, "name": raw.get("secName", ""),
                "shares": shares,
                "cost": cost, "price": price, "pnl": pnl,
            })
            codes_for_tdx.append(mx_code)

        # 3. TDX 实时价
        live_prices = {}
        if positions:
            print("📈 获取实时行情...")
            try:
                from mootdx.quotes import Quotes
                client = Quotes.factory(market='std')
                mdx = []
                for p in positions:
                    code = p["mx_code"]
                    mdx.append(f"{code}.SH" if code.startswith("6") else f"{code}.SZ")
                df = client.quotes(mdx)
                for _, row in df.iterrows():
                    raw = str(row["code"])
                    mkt = int(row.get("market", 0))
                    suffix = ".SH" if mkt == 1 else ".SZ"
                    live_prices[f"{raw}{suffix}"] = float(row.get("price", 0) or 0)
                client.close()
            except Exception as e:
                print(f"   ⚠️ TDX 失败: {e}")

        # 4. 组装盘点
        pos_lines = []
        total_pnl = 0
        for p in positions:
            real = live_prices.get(p["code"], p["price"])
            pnl_real = (real - p["cost"]) * p["shares"]
            total_pnl += pnl_real
            pos_lines.append(
                f"- {p['name']}({p['code']}): {p['shares']}股 成本¥{p['cost']:.2f} → 现价¥{real:.2f} 盈亏¥{pnl_real:+.2f}"
            )

        # 5. DeepSeek 总结
        prompt = f"""A股量化模拟盘盘中快报。账户: {balance['account_name']}。

总资产 ¥{balance['total_assets']:,.2f} | 可用 ¥{balance['avail_balance']:,.2f} | 仓位 {(balance['total_assets']-balance['avail_balance'])/balance['total_assets']*100:.0f}%

持仓({len(positions)}只):
{chr(10).join(pos_lines) if pos_lines else '空仓'}

总浮动盈亏: ¥{total_pnl:+,.2f}

请用100字以内总结:
1. 整体评价
2. 风险提示"""
        print("🤖 DeepSeek 分析...")
        llm_headers = {"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"}
        llm_payload = {"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "max_tokens": 300, "temperature": 0.3}
        async with s.post(DEEPSEEK_URL, json=llm_payload, headers=llm_headers) as r:
            llm_data = await r.json()
            summary = llm_data["choices"][0]["message"]["content"]
        print(f"   {summary[:150]}...")

        # 6. 推送企微
        print("📤 推送企业微信...")
        pos_pnl_str = " | ".join(
            f"{p['name'] or p['code'][:6]} {live_prices.get(p['code'],p['price']):.2f} {pnl:+7.0f}"
            for p, pnl in [
                (p, (live_prices.get(p['code'], p['price']) - p['cost']) * p['shares'])
                for p in positions
            ]
        ) if positions else "空仓"

        markdown = f"""## 河图 盘中持仓快报
> {datetime.now().strftime('%Y-%m-%d %H:%M')} | {balance['account_name']}
> 总资产 **¥{balance['total_assets']:,.0f}** | 仓位 {(balance['total_assets']-balance['avail_balance'])/balance['total_assets']*100:.0f}%

**持仓**: {pos_pnl_str}

---
**AI 总结**:
{summary}"""
        wh_resp = await mx_call(s, webhook, "", "", {"msgtype": "markdown", "markdown": {"content": markdown}})
        # WeCom webhook doesn't need apikey header, uses the URL key directly
        if isinstance(wh_resp, dict) and wh_resp.get("errcode") == 0:
            print("✅ 推送成功")
        else:
            # Retry with direct POST
            async with s.post(webhook, json={"msgtype": "markdown", "markdown": {"content": markdown}}) as r2:
                wh_resp2 = await r2.json()
                if wh_resp2.get("errcode") == 0:
                    print("✅ 推送成功")
                else:
                    print(f"⚠️ 推送失败: {wh_resp2}")

        print("✅ 完成")


if __name__ == "__main__":
    asyncio.run(main())
