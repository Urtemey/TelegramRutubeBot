import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

RUTUBE_SEARCH_URL = "https://rutube.ru/api/search/video/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_vk_executor = ThreadPoolExecutor(max_workers=1)


# ── Rutube (JSON API, no auth) ─────────────────────────────────────────────────

async def search_rutube(query: str, count: int = 10) -> list[dict]:
    params = {"query": query, "format": "json", "page": 1}
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=12) as client:
            resp = await client.get(RUTUBE_SEARCH_URL, params=params)
            if resp.status_code != 200:
                logger.warning("Rutube: статус %s", resp.status_code)
                return []
            data = resp.json()
    except Exception as e:
        logger.error("Rutube ошибка: %s", e)
        return []

    results = []
    for item in data.get("results", []):
        if item.get("is_adult") or item.get("is_paid"):
            continue
        title = item.get("title", "Без названия")
        description = (item.get("description") or "").strip()
        if len(description) > 250:
            description = description[:250] + "..."
        video_url = item.get("video_url", "")
        duration = (item.get("duration") or 0)
        results.append({
            "source": "Rutube",
            "title": title,
            "description": description,
            "url": video_url,
            "duration_min": duration // 60,
        })
        if len(results) >= count:
            break
    return results


# ── VK Video (Selenium + Chrome profile) ──────────────────────────────────────

def _get_chrome_driver():
    
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    profile_path = os.getenv("CHROME_PROFILE_PATH", "").strip()
    if profile_path:
        options.add_argument(f"--user-data-dir={profile_path}")
        profile_dir = os.getenv("CHROME_PROFILE_DIR", "Default").strip()
        options.add_argument(f"--profile-directory={profile_dir}")
        logger.info("VK: используем профиль Chrome: %s / %s", profile_path, profile_dir)
    else:
        logger.warning("VK: CHROME_PROFILE_PATH не задан, авторизация может потребоваться")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def _parse_duration(text: str) -> int:
    """Парсит '1:35:23' или '55:12' в минуты."""
    text = text.strip().splitlines()[-1].strip()  # берём последнюю строку (убираем 'FHD' и пр.)
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0])
        if len(parts) == 3:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        pass
    return 0


def _scrape_vk_sync(query: str, count: int = 10) -> list[dict]:
    """Синхронный скрейпинг VK Video (запускается в отдельном потоке)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    url = f"https://vk.com/video?q={quote_plus(query)}&section=search"
    driver = None
    results = []

    try:
        driver = _get_chrome_driver()
        driver.get(url)

        # Ждём появления карточек по реальному data-testid
        wait = WebDriverWait(driver, 12)
        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, '[data-testid="video_card_layout"]')
        ))

        cards = driver.find_elements(By.CSS_SELECTOR, '[data-testid="video_card_layout"]')
        logger.info("VK: найдено %d карточек", len(cards))

        for card in cards[:count * 3]:
            try:
                # Все ссылки на видео (формат vkvideo.ru/video...)
                video_links = card.find_elements(
                    By.CSS_SELECTOR, 'a[href*="vkvideo.ru/video"]'
                )
                if not video_links:
                    continue

                # Первая ссылка — превью (в тексте содержит длительность)
                thumb_link = video_links[0]
                url_video = thumb_link.get_attribute("href") or ""
                duration_min = _parse_duration(thumb_link.text)

                # Вторая ссылка — заголовок (если есть), иначе тоже первая
                title_link = video_links[1] if len(video_links) > 1 else video_links[0]
                title = title_link.text.strip()
                if not title:
                    title = title_link.get_attribute("title") or "Без названия"

                if not title or not url_video:
                    continue

                results.append({
                    "source": "VK Video",
                    "title": title,
                    "description": "",
                    "url": url_video,
                    "duration_min": duration_min,
                })

                if len(results) >= count:
                    break

            except Exception as e:
                logger.debug("VK: ошибка парсинга карточки: %s", e)
                continue

    except Exception as e:
        logger.error("VK Selenium ошибка: %s", e)
    finally:
        if driver:
            driver.quit()

    return results


async def search_vk(query: str, count: int = 5) -> list[dict]:
    """Асинхронная обёртка с жёстким таймаутом 35 секунд."""
    profile_path = os.getenv("CHROME_PROFILE_PATH", "").strip()
    if not profile_path:
        return []
    loop = asyncio.get_event_loop()
    try:
        results = await asyncio.wait_for(
            loop.run_in_executor(_vk_executor, lambda: _scrape_vk_sync(query, count)),
            timeout=35,
        )
        return results
    except asyncio.TimeoutError:
        logger.warning("VK: таймаут 35 сек, пропускаю")
        return []
    except Exception as e:
        logger.error("VK async ошибка: %s", e)
        return []


# ── Combined ───────────────────────────────────────────────────────────────────

async def search_all(query: str, count: int = 5) -> list[dict]:
    rutube_task = asyncio.create_task(search_rutube(query, count))
    vk_task = asyncio.create_task(search_vk(query, count))
    rutube, vk = await asyncio.gather(rutube_task, vk_task)

    if not rutube and not vk:
        fallback = query.split()[0] if " " in query else query
        rutube = await search_rutube(fallback, count)

    # Чередуем: Rutube, VK, Rutube, VK...
    combined = []
    for i in range(max(len(rutube), len(vk))):
        if i < len(rutube):
            combined.append(rutube[i])
        if i < len(vk):
            combined.append(vk[i])
    return combined
