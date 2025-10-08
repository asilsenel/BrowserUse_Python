# check_page.py (fixed)
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path("./diag"); OUT.mkdir(exist_ok=True)
URL = "https://www.kktcmerkezbankasi.org/tr/veriler/doviz_kurlari/kur_sorgulama"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--lang=tr-TR"])
        ctx = await browser.new_context(
            locale="tr-TR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
            accept_downloads=True,
            ignore_https_errors=True,
        )
        page = await ctx.new_page()

        # Console / errors / network diyalogları
        page.on("console", lambda msg: print("[console]", msg.type, msg.text))
        page.on("pageerror", lambda e: print("[pageerror]", e))
        page.on("requestfailed", lambda r: print("[requestfailed]", r.url, r.failure))

        print("goto:", URL)
        resp = await page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        print("status:", resp.status if resp else None)

        # İlk yüklemeden sonra kısa bekle, gerekirse bir kez refresh
        await page.wait_for_timeout(2000)
        # Çerez/popup varsa kapatmaya çalış
        for sel in [
            "text=Kabul Et", "text=Kabul", "text=Tamam", "text=Anladım",
            "[aria-label*=kapat i]", "[aria-label*=kapat]", "button:has-text('×')"
        ]:
            try:
                if await page.locator(sel).first.is_visible():
                    await page.locator(sel).first.click()
                    await page.wait_for_timeout(500)
            except Exception:
                pass

        # Boş kalıyorsa bir kez reload
        if not await page.locator("text=Kur Sorgulama").first.is_visible():
            await page.reload(wait_until="networkidle")
            await page.wait_for_timeout(1500)

        # Teşhis çıktıları
        await page.screenshot(path=OUT / "first.png", full_page=True)
        html = await page.content()
        (OUT / "first.html").write_text(html, encoding="utf-8")
        print("saved:", OUT / "first.png", OUT / "first.html")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
