# Utility script to inspect and dump target web application elements
import time
from playwright.sync_api import sync_playwright


with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir="user_data",
        headless=False
    )
    page = context.pages[0] if context.pages else context.new_page()

    print("\n1. Login in the browser window.")
    print("2. Navigate to the lessons/modules page.")
    print("3. Press Enter in this terminal to scan the page elements...\n")
    input("Press Enter when ready → ")

    print("\nScanning buttons on the page...")
    buttons = page.locator("button").all()
    print(f"Found {len(buttons)} button elements:")
    for idx, btn in enumerate(buttons):
        try:
            text = btn.inner_text().strip().replace("\n", " ")
            html = btn.evaluate("el => el.outerHTML")
            title = btn.get_attribute("title")
            cls = btn.get_attribute("class")
            print(f"\nButton #{idx+1}:")
            print(f"  Text: {text}")
            print(f"  Title: {title}")
            print(f"  Class: {cls}")
            print(f"  HTML: {html[:200]}")
        except Exception as e:
            print(f"  Error reading button #{idx+1}: {e}")

    print("\nScanning all elements with text 'Practice'...")
    practice_elements = page.locator("*:has-text('Practice')").all()
    print(f"Found {len(practice_elements)} elements containing 'Practice':")
    for idx, el in enumerate(practice_elements[:15]):
        try:
            tag = el.evaluate("el => el.tagName")
            cls = el.get_attribute("class")
            html = el.evaluate("el => el.outerHTML")
            print(f"\nElement #{idx+1}:")
            print(f"  Tag: {tag}")
            print(f"  Class: {cls}")
            print(f"  HTML: {html[:150]}")
        except Exception as e:
            pass

    input("\nDone. Press Enter to close browser...")
