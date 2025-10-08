import os
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from openai import OpenAI
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from dotenv import load_dotenv

# .env yükle (OPENAI_API_KEY burada olmalı)
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DESKTOP = Path.home() / "Desktop"
OUT_DIR = DESKTOP / "KKTCMB_Downloads"
OUT_DIR.mkdir(parents=True, exist_ok=True)

URL = "https://www.kktcmerkezbankasi.org/tr/veriler/doviz_kurlari/kur_sorgulama"

def tr_date(d: datetime) -> str:
    return d.strftime("%d/%m/%Y")

TODAY = datetime.now()
START = TODAY - timedelta(days=3)

# ---- Helper’lar ----
async def safe_click(page, selectors, timeout=4000):
    """selectors: str veya [str, ...]  —  İlk görünür olanı tıklar."""
    if isinstance(selectors, str):
        selectors = [selectors]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.click()
            return True
        except Exception:
            continue
    return False


async def safe_fill(page, selectors, value, timeout=4000, clear=True):
    """Tarih ve metin girişi için; ilk tutan input’a doldurur."""
    if isinstance(selectors, str):
        selectors = [selectors]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            if clear:
                try:
                    await loc.fill("")  # clear
                except Exception:
                    pass
            await loc.fill(value)
            await page.wait_for_timeout(200)
            return True
        except Exception:
            continue
    return False


async def select_currency_llm(page, user_input: str):
    """
    Dropdown listesini okur → LLM ile kullanıcı girdisiyle en yakın eşleşmeyi bulur → seçer.
    """
    sel = "select#edit-kur-kod"
    try:
        await page.locator(sel).wait_for(state="visible", timeout=3000)
        options = await page.locator(sel + " option").all_inner_texts()
        print(f"\n🔍 Mevcut kurlar: {options}")

        prompt = f"""
        Kullanıcı döviz olarak '{user_input}' girdi.
        Aşağıdaki liste KKTC MB sitesindeki döviz isimleridir:
        {options}
        Listedekilerden hangisi kullanıcı girdisine en yakın dövizdir?
        Cevabın sadece listedeki orijinal ifadeyi içersin, başka açıklama yazma.
        """
        response = client.responses.create(
            model="gpt-4o-mini",
            input=prompt,
            temperature=0
        )
        best_match = response.output_text.strip()
        print(f"🎯 LLM seçimi: {best_match}")

        await page.locator(sel).select_option(label=best_match)
        print(f"✅ Dropdown seçildi: {best_match}")
        return True
    except Exception as e:
        print("⚠️ LLM destekli seçim başarısız:", e)
        return False


async def wait_table_or_result(page):
    """'Listele' sonrası bir tablo veya sonuç paneli bekle."""
    for sel in [
        "table", "table tbody tr",
        "div:has-text('Kayıt')",
        "div.dataTables_wrapper",
    ]:
        try:
            await page.locator(sel).first.wait_for(state="visible", timeout=7000)
            return True
        except Exception:
            continue
    return False


async def download_click_and_save(page, btn_selectors, save_path: Path):
    """Excel indir butonuna tıkla, indirme event’ini yakala, kaydet."""
    async with page.expect_download(timeout=15000) as dl_info:
        clicked = await safe_click(page, btn_selectors, timeout=5000)
        if not clicked:
            raise RuntimeError("EXCEL İndir butonu bulunamadı.")
    download = await dl_info.value
    suggested = download.suggested_filename
    target = save_path / (suggested or "indirilen.xlsx")
    await download.save_as(target)
    return target


# ---- Ana akış ----
async def run():
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
        await page.goto(URL, wait_until="domcontentloaded", timeout=120_000)

        # Çerez / popup kapat
        await safe_click(page, [
            "text=Kabul Et",
            "text=Kabul",
            "text=Tamam",
            "text=Anladım",
            "[aria-label*=kapat i]",
            "[aria-label*=kapat]",
            "button:has-text('×')"
        ], timeout=2000)

        # --- 1) Tarih Bazında Kur Sorgulama -> EXCEL İndir ---
        await safe_click(page, [
            "a:has-text('Tarih Bazında Kur Sorgulama')",
            "button:has-text('Tarih Bazında Kur Sorgulama')",
            "text=Tarih Bazında Kur Sorgulama"
        ], timeout=2000)

        tarih_excel = await download_click_and_save(
            page,
            btn_selectors=[
                "a:has-text('EXCEL İndir')",
                "button:has-text('EXCEL İndir')",
                "text=EXCEL İndir"
            ],
            save_path=OUT_DIR
        )
        print(f"✅ Tarih bazında indirildi: {tarih_excel.name}")

        # --- 2) Döviz Cinsi Bazında ---
        await safe_click(page, [
            "a:has-text('Döviz Cinsi Bazında Kur Sorgulama')",
            "button:has-text('Döviz Cinsi Bazında Kur Sorgulama')",
            "text=Döviz Cinsi Bazında Kur Sorgulama"
        ], timeout=4000)

        # Tarih alanları doldur
        start_ok = await safe_fill(page, [
            "input[name*=Baslangic]", "input#BaslangicTarihi",
            "input[placeholder*='Başlangıç']", "input[name*=start]", "input[name*=Start]",
            "input[type='text'] >> nth=0"
        ], tr_date(START), timeout=3000)

        end_ok = await safe_fill(page, [
            "input[name*=Bitis]", "input#BitisTarihi",
            "input[placeholder*='Bitiş']", "input[name*=end]", "input[name*=End]",
            "input[type='text'] >> nth=1"
        ], tr_date(TODAY), timeout=3000)

        if not (start_ok and end_ok):
            print("⚠️ Tarih alanları otomatik bulunamadı, tarih picker olabilir.")

        # 🔹 Kullanıcıdan döviz iste
        user_input = input("💬 Hangi döviz birimi seçilsin? (ör. 'İsveç Kronu', 'SEK', 'isvec'): ")
        await select_currency_llm(page, user_input)

        # Listele
        listed = await safe_click(page, [
            "button:has-text('Listele')",
            "input[type='submit'][value='Listele']",
            "text=Listele"
        ], timeout=3000)
        if not listed:
            print("⚠️ 'Listele' butonu bulunamadı, tablo zaten açık olabilir.")

        await wait_table_or_result(page)

        # Excel indir (ikinci)
        doviz_excel = await download_click_and_save(
            page,
            btn_selectors=[
                "a:has-text('EXCEL İndir')",
                "button:has-text('EXCEL İndir')",
                "text=EXCEL İndir"
            ],
            save_path=OUT_DIR
        )
        print(f"✅ Döviz cinsi bazında indirildi: {doviz_excel.name}")

        print(f"\n📂 Klasör: {OUT_DIR}")
        await ctx.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
