# kktcmb_worker.py
import os
import re
import json
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from playwright.async_api import async_playwright

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DESKTOP = Path.home() / "Desktop"
OUT_DIR = DESKTOP / "KKTCMB_Downloads"
OUT_DIR.mkdir(parents=True, exist_ok=True)

URL = "https://www.kktcmerkezbankasi.org/tr/veriler/doviz_kurlari/kur_sorgulama"


def tr_date(d: datetime) -> str:
    return d.strftime("%d/%m/%Y")


def parse_json_relaxed(text: str):
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


async def send_safe(send_log, msg):
    try:
        await send_log(msg)
    except Exception:
        pass


async def close_cookies(page, send_log):
    for sel in [
        "text=Kabul Et", "text=Kabul", "text=Tamam", "text=Anladım",
        "[aria-label*=kapat i]", "[aria-label*=kapat]", "button:has-text('×')"
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click()
                await page.wait_for_timeout(200)
                await send_safe(send_log, f"🧹 Çerez/popup kapatıldı: {sel}")
                break
        except Exception:
            continue


async def select_currency_llm(page, user_input: str, send_log):
    sel = "select#edit-kur-kod"
    await page.locator(sel).wait_for(state="visible", timeout=5000)
    options = await page.locator(f"{sel} option").all_inner_texts()
    await send_safe(send_log, f"🔍 Mevcut kurlar: {options}")

    sys = ("You are a precise extraction assistant. Given a user currency hint and a list of official "
           "currency display names, return exactly one item from the list that best matches the hint. "
           "Return ONLY the chosen list string, nothing else.")
    user = f"User hint: {user_input}\nList: {options}\nReturn exactly one item from the list."

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
    )
    best_match = resp.choices[0].message.content.strip()
    await send_safe(send_log, f"🎯 LLM seçimi: {best_match}")

    # label ile dene
    try:
        await page.locator(sel).select_option(label=best_match)
        await send_safe(send_log, f"✅ Dropdown seçildi: {best_match}")
        return best_match
    except Exception as e:
        await send_safe(send_log, f"⚠️ Dropdown seçimi başarısız (label): {e}")

    # ISO ile dene (parantez içi)
    m = re.search(r"\(([A-Z]{3})\)", best_match or "")
    if m:
        code = m.group(1)
        try:
            all_values = await page.locator(f"{sel} option").evaluate_all(
                "els => els.map(e => ({v:e.value,t:(e.textContent||'').trim()}))"
            )
            value_by_code = next((o["v"] for o in all_values if f"({code})" in o["t"]), None)
            if value_by_code:
                await page.locator(sel).select_option(value=value_by_code)
                await send_safe(send_log, f"✅ ISO ile seçildi: {code} (value={value_by_code})")
                return best_match
        except Exception as e2:
            await send_safe(send_log, f"⚠️ ISO value seçimi de başarısız: {e2}")

    return None


async def _type_into(page, locator_str: str, value: str):
    """Klavyeyle yaz: click -> Ctrl/Cmd+A -> Backspace -> type -> Enter -> Tab"""
    loc = page.locator(locator_str).first
    await loc.wait_for(state="visible", timeout=1500)
    await loc.click()
    # Mac'te Meta, Windows'ta Control
    try:
        await page.keyboard.press("Meta+A")
    except Exception:
        await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await loc.type(value, delay=30)
    await page.keyboard.press("Enter")
    await page.keyboard.press("Tab")
    return True


async def set_dates_resilient(page, start_dt: datetime, end_dt: datetime, send_log):
    """datepicker/readonly/gizli alan fark etmeksizin tarihleri gerçekten uygular."""
    start_str = tr_date(start_dt)
    end_str = tr_date(end_dt)

    candidates_start = [
        "input[name*=Baslangic]", "#BaslangicTarihi", "#edit-baslangic-tarihi",
        "input[name='baslangic_tarihi']", "input[placeholder*='Başlangıç']",
        "input[name*=start]", "input[name*=Start]",
        "input.hasDatepicker >> nth=0", "input[type='text'] >> nth=0",
    ]
    candidates_end = [
        "input[name*=Bitis]", "#BitisTarihi", "#edit-bitis-tarihi",
        "input[name='bitis_tarihi']", "input[placeholder*='Bitiş']",
        "input[name*=end]", "input[name*=End]",
        "input.hasDatepicker >> nth=1", "input[type='text'] >> nth=1",
    ]

    wrote = False
    # 1) klavye yöntemi
    for s_sel, e_sel in zip(candidates_start, candidates_end):
        try:
            await _type_into(page, s_sel, start_str)
            await _type_into(page, e_sel, end_str)
            wrote = True
            await send_safe(send_log, f"⌨️ Klavye ile yazıldı: {start_str} → {end_str}  ({s_sel} , {e_sel})")
            break
        except Exception:
            continue

    # 2) jQuery/JS ile value set + event tetikleme + readonly kaldırma
    if not wrote:
        try:
            await page.evaluate(
                """
                (start, end, sels) => {
                  const [starts, ends] = sels;
                  function setVal(el, val){
                    if(!el) return false;
                    try { el.removeAttribute('readonly'); } catch(e){}
                    el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                    el.dispatchEvent(new Event('blur', {bubbles:true}));
                    if (window.$ && typeof $(el).datepicker === 'function') {
                      try { $(el).datepicker('setDate', val); } catch(e){}
                    }
                    return true;
                  }
                  let ok = false;
                  for (const q of starts){
                    const el = document.querySelector(q);
                    if (setVal(el, start)) { ok = true; break; }
                  }
                  let ok2 = false;
                  for (const q of ends){
                    const el = document.querySelector(q);
                    if (setVal(el, end)) { ok2 = true; break; }
                  }
                  // gizli alanlar
                  const hiddenLike = Array.from(document.querySelectorAll("input[type='hidden']"))
                    .filter(el => /baslangic|bitis|start|end|tarih/i.test(el.name || el.id || ""));
                  hiddenLike.forEach(h => {
                    if(/baslangic|start/i.test(h.name||h.id)) h.value = start;
                    if(/bitis|end/i.test(h.name||h.id)) h.value = end;
                    h.dispatchEvent(new Event('change', {bubbles:true}));
                  });
                  // varsa 'Uygula' butonuna bas
                  const applyBtn = Array.from(document.querySelectorAll("button, a, input[type='button']"))
                    .find(b => /Uygula|Tamam|Apply/i.test(b.textContent || b.value || ""));
                  if (applyBtn) applyBtn.click();
                  return ok && ok2;
                }
                """,
                start_str, end_str, [candidates_start, candidates_end]
            )
            wrote = True
            await send_safe(send_log, f"🧠 JS/datepicker ile yazıldı: {start_str} → {end_str}")
        except Exception:
            pass

    # 3) doğrulama: input_value() gerçekten bizim yazdığımız mı?
    # (bulabildiğimiz ilk eşleşen iki input’tan kontrol)
    def read_back(sel_list):
        for q in sel_list:
            try:
                val = page.locator(q).first.input_value()
                return val
            except Exception:
                continue
        return None

    try:
        s_val = await read_back(candidates_start)
        e_val = await read_back(candidates_end)
        await send_safe(send_log, f"🔎 Ekrandaki değerler: {s_val} → {e_val}")
    except Exception:
        s_val = e_val = None

    # 4) hâlâ ekran varsayılanı görünüyorsa kullanıcıyı uyar ama devam et
    if (s_val and s_val != start_str) or (e_val and e_val != end_str):
        await send_safe(send_log, "⚠️ Ekran değerleri hedef tarihlerle tam eşleşmedi; devam ediyorum (form submit'te güncellenebilir).")

    return wrote


async def run_kktcmb(prompt_text: str, send_log):
    await send_safe(send_log, f"💬 Prompt: {prompt_text}")

    # — intent + tarih + kur extraction —
    today_str = tr_date(datetime.now())
    sys = (
        "You are an intelligent extractor for currency report automation.\n"
        "Extract:\n"
        "- mode: 'all' (all currencies for one date), 'single' (one currency for date range), or 'both'.\n"
        f"- start_date and end_date in dd/MM/yyyy (handle Turkish: 'bugün','dün','son 3 gün'). Today is {today_str}.\n"
        "- currency: if mode!='all'.\n"
        "ALWAYS output STRICT JSON with keys: mode, start_date, end_date, currency."
    )
    user = f'Kullanıcı mesajı: """{prompt_text}"""'

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
    )
    raw = resp.choices[0].message.content
    data = parse_json_relaxed(raw)

    if not data:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=3)
        data = {"mode": "single", "start_date": tr_date(start_dt), "end_date": tr_date(end_dt), "currency": "İsveç Kronu"}
        await send_safe(send_log, f"⚠️ JSON çözülemedi, varsayılan: {data}")
    else:
        await send_safe(send_log, f"📦 Çıkarılan parametreler: {data}")

    mode = (data.get("mode") or "single").lower()
    currency_hint = data.get("currency") or "İsveç Kronu"

    try:
        start_date = datetime.strptime(data["start_date"], "%d/%m/%Y")
        end_date = datetime.strptime(data["end_date"], "%d/%m/%Y")
    except Exception:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=3)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--lang=tr-TR"])
        ctx = await browser.new_context(locale="tr-TR", accept_downloads=True, ignore_https_errors=True)
        page = await ctx.new_page()

        await send_safe(send_log, "🌐 Sayfaya gidiliyor…")
        await page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        await close_cookies(page, send_log)

        # A) tüm kurlar (mode: all/both)
        if mode in ["all", "both"]:
            await send_safe(send_log, "➡️ Tarih Bazında Kur Sorgulama (Tüm kurlar)")
            await page.click("text=Tarih Bazında Kur Sorgulama")
            async with page.expect_download(timeout=20000) as d1:
                await page.click("text=EXCEL İndir")
            d1 = await d1.value
            f1 = OUT_DIR / d1.suggested_filename
            await d1.save_as(f1)
            await send_safe(send_log, f"✅ Tüm kurlar Downloads klasörüne indirildi: {f1.name}")

        # B) tek kur (mode: single/both)
        if mode in ["single", "both"]:
            await send_safe(send_log, "➡️ Döviz Cinsi Bazında Kur Sorgulama (tek kur)")
            await page.click("text=Döviz Cinsi Bazında Kur Sorgulama")

            ok_dates = await set_dates_resilient(page, start_date, end_date, send_log)
            if not ok_dates:
                await send_safe(send_log, "⚠️ Tarihler güvence altına alınamadı; yine de devam ediyorum.")

            await select_currency_llm(page, currency_hint, send_log)

            try:
                await page.click("text=Listele", timeout=6000)
            except Exception:
                await send_safe(send_log, "ℹ️ 'Listele' görünmüyor, tablo yüklü olabilir.")

            async with page.expect_download(timeout=25000) as d2:
                await page.click("text=EXCEL İndir")
            d2 = await d2.value
            f2 = OUT_DIR / d2.suggested_filename
            await d2.save_as(f2)
            await send_safe(send_log, f"✅ Tek kur Downloads klasörüne indirildi: {f2.name}")

        await ctx.close()
        await browser.close()

    await send_safe(send_log, "🎉 İşlem tamamlandı.")
    return {"mode": mode, "start_date": tr_date(start_date), "end_date": tr_date(end_date), "currency": currency_hint}
