import asyncio
from playwright.async_api import async_playwright

URL = "https://www.kktcmerkezbankasi.org/tr/veriler/doviz_kurlari/kur_sorgulama"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--lang=tr-TR"])
        ctx = await browser.new_context(locale="tr-TR", accept_downloads=True)
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Döviz Cinsi Bazında sekmesine geç
        await page.click("text=Döviz Cinsi Bazında Kur Sorgulama")
        await page.wait_for_timeout(1000)

        # Dropdown veya select benzeri elementleri göster
        candidates = [
            "select",
            "button[role='combobox']",
            "div[role='button']",
            "div.dropdown-toggle",
            "ul li",
            "div[role='option']",
        ]
        for sel in candidates:
            count = await page.locator(sel).count()
            if count:
                print(f"\n---- {sel} ({count}) ----")
                htmls = await page.locator(sel).evaluate_all("els => els.map(e => e.outerHTML)")
                for h in htmls[:10]:  # fazla uzun olmasın
                    print(h[:300].replace("\n", " "))
        print("\n✅ Tamamlandı.")
        await browser.close()

asyncio.run(main())
