# Speech Media Automation Suite - Core Dialog and Audio Engine
import json
import os
import random

import time
import sys
import subprocess

# 1. Automatically install missing Python packages
required_packages = {
    "playwright": "playwright",
    "groq": "groq",
    "python-dotenv": "dotenv",
    "gtts": "gtts"
}

for package, import_name in required_packages.items():
    try:
        __import__(import_name)
    except ImportError:
        print(f"  [SETUP] Installing missing library '{package}'... Please wait.")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
            print(f"  ✓ Installed '{package}' successfully.")
        except Exception as e:
            print(f"  [ERROR] Failed to install package '{package}': {e}")
            sys.exit(1)

# 2. Automatically install Chromium driver for Playwright
try:
    print("  [SETUP] Ensuring Playwright Chromium browser is installed...")
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    print("  ✓ Playwright Chromium browser is ready.")
except Exception as e:
    print(f"  [WARN] Playwright browser setup message: {e}")

# 3. Load or Prompt for Groq API Key
from dotenv import load_dotenv
from groq import Groq
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()
api_key = os.getenv("GROQ_API_KEY")

if not api_key or "your_" in api_key.lower() or api_key.strip() == "":
    print("\n" + "="*60)
    print("  [API SETUP] Groq API Key not found!")
    print("  Get a free key from: https://console.groq.com/")
    api_key = input("  Please paste your GROQ_API_KEY here: ").strip()
    if api_key:
        with open(".env", "w") as f:
            f.write(f"GROQ_API_KEY={api_key}\n")
        print("  ✓ Saved API Key to .env file.")
    else:
        print("  [ERROR] API Key is required to run the solver.")
        sys.exit(1)

client = Groq(api_key=api_key)
MODEL = "llama-3.1-8b-instant"
CACHE_FILE = "cache.json"
track_url = ""
ASSIST_MODE = True  # If True, script only fills/records answers and lets user click Check/Continue

def extract_lessons_url(url):
    if "/exam/" in url:
        # Reconstruct lessons URL from exam URL
        # e.g., .../lessons/498616/exam/505325?sectionId=3&unitId=82
        parts = url.split("/exam/")
        if len(parts) == 2:
            base = parts[0]
            query = ""
            if "?" in parts[1]:
                query = "?" + parts[1].split("?", 1)[1]
            return base + query
    return ""

def click_practice_with_retry(page, title):
    for attempt in range(3):
        try:
            print(f"  [Attempt {attempt+1}] Trying to click Practice button for '{title}'...")
            clicked = page.evaluate("""(lessonTitle) => {
                let cardElements = Array.from(document.querySelectorAll('.card, .card-body'));
                let card = cardElements.find(c => (c.innerText || '').includes(lessonTitle));
                if (!card) return 'card_not_found';
                
                let btn = card.querySelector('[title="Practice"]');
                if (!btn) {
                    let clickable = Array.from(card.querySelectorAll('button, div, span, a, svg, img'));
                    btn = clickable.find(el => {
                        let txt = (el.innerText || '').trim();
                        let titleAttr = el.getAttribute('title') || '';
                        let ariaAttr = el.getAttribute('aria-label') || '';
                        return txt === 'Practice' || titleAttr.includes('Practice') || ariaAttr.includes('Practice');
                    });
                }
                
                if (btn) {
                    btn.scrollIntoView({ block: 'center' });
                    btn.click();
                    return 'clicked';
                }
                return 'button_not_found';
            }""", title)
            
            print(f"  [DEBUG] JS click result: {clicked}")
            if clicked == 'clicked':
                return True
            time.sleep(1)
        except Exception as e:
            print(f"  [WARN] Click Practice attempt {attempt+1} failed: {e}. Retrying in 1s...")
            time.sleep(1)
    return False

def click_unlock_with_retry(page, title):
    for attempt in range(3):
        try:
            print(f"  [Attempt {attempt+1}] Trying to click Unlock button for '{title}'...")
            clicked = page.evaluate("""(lessonTitle) => {
                let cardElements = Array.from(document.querySelectorAll('.card, .card-body'));
                let card = cardElements.find(c => (c.innerText || '').includes(lessonTitle));
                if (!card) return 'card_not_found';
                
                let btn = card.querySelector('[title="Unlock"]');
                if (!btn) {
                    let clickable = Array.from(card.querySelectorAll('button, div, span, a, svg, img'));
                    btn = clickable.find(el => {
                        let txt = (el.innerText || '').trim();
                        let titleAttr = el.getAttribute('title') || '';
                        let ariaAttr = el.getAttribute('aria-label') || '';
                        return txt === 'Unlock' || titleAttr.includes('Unlock') || ariaAttr.includes('Unlock');
                    });
                }
                
                if (btn) {
                    btn.scrollIntoView({ block: 'center' });
                    btn.click();
                    return 'clicked';
                }
                return 'button_not_found';
            }""", title)
            
            print(f"  [DEBUG] JS click result: {clicked}")
            if clicked == 'clicked':
                time.sleep(2)
                modal_clicked = page.evaluate("""() => {
                    let buttons = Array.from(document.querySelectorAll('button, div, span, a'));
                    for (let mb of buttons) {
                        let txt = (mb.innerText || '').trim().toLowerCase();
                        if (['confirm', 'yes', 'ok', 'unlock'].some(k => txt === k || txt.includes(k))) {
                            mb.click();
                            return 'modal_clicked';
                        }
                    }
                    return 'no_modal';
                }""")
                print(f"  [DEBUG] Modal click result: {modal_clicked}")
                time.sleep(2)
                page.reload()
                time.sleep(4)
                return True
            time.sleep(1)
        except Exception as e:
            print(f"  [WARN] Click Unlock attempt {attempt+1} failed: {e}. Retrying in 1s...")
            time.sleep(1)
    return False
modules_completed = 0
current_module_name = "Unknown Module"
completed_in_session = set()
audio_transcript_cache = {}

def is_question_page(page_or_frame):
    try:
        url = page_or_frame.url.lower()
        if url.endswith("/home") or url.endswith("/units"):
            return False
        if "/lessons" in url and "/exam" not in url and "/test" not in url:
            return False
        return True
    except Exception:
        pass
    return False

def has_active_question(frame_or_page, main_page):
    try:
        if not is_question_page(main_page):
            return False
            
        # Run all checks in a single fast JS call to prevent slow WebSocket round-trips
        res = frame_or_page.evaluate(r"""() => {
            let has_inputs = false;
            let inputs = Array.from(document.querySelectorAll('textarea:not([readonly]), [contenteditable="true"], div[role="textbox"]'));
            for (let el of inputs) {
                let rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    has_inputs = true;
                }
            }

            let has_mic = false;
            let clickable = Array.from(document.querySelectorAll('button, div, span, a, svg'));
            for (let el of clickable) {
                let rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    let txt = (el.innerText || '').trim().toLowerCase();
                    let title = (el.getAttribute('title') || '').trim().toLowerCase();
                    let aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
                    let keywords = ['record', 'speak', 'tap to speak', 'microphone'];
                    let isMatch = keywords.some(kw => txt.includes(kw) || title.includes(kw) || aria.includes(kw));
                    if (isMatch) {
                        has_mic = true;
                        break;
                    }
                }
            }

            let choicesCount = 0;
            let divs = Array.from(document.querySelectorAll('div'));
            for (let d of divs) {
                let classStr = d.getAttribute('class') || '';
                let txt = (d.innerText || '').trim();
                if (classStr.includes('cursor-pointer')) {
                    let firstWord = txt.split(/\s+/)[0] || '';
                    if (firstWord.match(/^\d+$/) && txt.length > 3 && txt.length < 250) {
                        let hasChildOption = Array.from(d.querySelectorAll('div')).some(child => {
                            let cCls = child.getAttribute('class') || '';
                            return cCls.includes('cursor-pointer');
                        });
                        if (!hasChildOption) {
                            choicesCount++;
                        }
                    }
                }
            }
            let has_mcq = choicesCount >= 2;

            return {
                has_inputs,
                has_mic,
                has_mcq,
                inputs_count: inputs.length,
                choices_count: choicesCount,
                url: window.location.href
            };
        }""")
        has_q = res['has_inputs'] or res['has_mic'] or res['has_mcq']
        if is_question_page(main_page):
            # Print frame debug
            print(f"    [DEBUG] Frame: '{res['url']}' | inputs: {res['inputs_count']} ({res['has_inputs']}) | mic: {res['has_mic']} | choices: {res['choices_count']} ({res['has_mcq']}) -> detected: {has_q}")
        return has_q
    except Exception as e:
        # print(f"    [DEBUG] Exception in has_active_question: {e}")
        pass

    return False

_last_page_urls = []

def find_active_question_frame(context):
    global _last_page_urls
    try:
        current_urls = []
        for p in context.pages:
            try:
                current_urls.append(p.url)
            except Exception:
                pass
        if current_urls != _last_page_urls:
            print(f"  [DEBUG] Open tabs: {current_urls}")
            _last_page_urls = current_urls
    except Exception:
        pass

    for p in context.pages:
        try:
            # Check child frames first
            for frame in p.frames:
                try:
                    if frame == p.main_frame:
                        continue
                    if has_active_question(frame, p):
                        return frame
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if has_active_question(p.main_frame, p):
                return p.main_frame
        except Exception:
            pass

    return None


def get_audio_transcript(page_or_frame):
    page = page_or_frame
    global audio_transcript_cache
    try:
        # Find audio URL from page elements
        audio_url = page.evaluate("""() => {
            let audioEl = document.querySelector('audio');
            let url = '';
            if (audioEl) {
                url = audioEl.src || (audioEl.querySelector('source') ? audioEl.querySelector('source').src : '');
            }
            if (!url) {
                let links = Array.from(document.querySelectorAll('a, source, embed, iframe, button'));
                for (let el of links) {
                    let src = el.src || el.href || el.getAttribute('src') || el.getAttribute('href') || el.getAttribute('data-src') || '';
                    if (src && src.match(/\\.(mp3|wav|m4a|ogg|mp4|webm)(\\?|$)/i)) {
                        url = src;
                        break;
                    }
                }
            }
            return url;
        }""")
        
        if not audio_url:
            return None
            
        if audio_url in audio_transcript_cache:
            print("  [AUDIO] Using cached audio transcript...")
            return audio_transcript_cache[audio_url]
            
        print(f"  [AUDIO] Found audio URL: {audio_url}")
        print("  [AUDIO] Downloading audio bytes via Python urllib...")
        
        try:
            user_agent = page.evaluate("navigator.userAgent")
        except Exception:
            user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            
        import urllib.request
        import io
        
        try:
            req = urllib.request.Request(
                audio_url, 
                headers={
                    "User-Agent": user_agent,
                    "Referer": "https://corporate.bharatenglish.org/"
                }
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                audio_bytes = response.read()
        except Exception as download_err:
            print(f"  [AUDIO] Error downloading audio: {download_err}")
            return None
            
        print("  [AUDIO] Transcribing audio with Groq Whisper...")
        
        # Call Groq Whisper API
        transcription = client.audio.transcriptions.create(
            file=("audio.mp3", io.BytesIO(audio_bytes), "audio/mpeg"),
            model="whisper-large-v3"
        )
        
        transcript = transcription.text
        print(f"  [AUDIO] Transcript: {transcript}")
        audio_transcript_cache[audio_url] = transcript
        return transcript
    except Exception as e:
        print(f"  [AUDIO] Error transcribing audio: {e}")
        return None


if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r") as f:
        cache = json.load(f)
else:
    cache = {}

def save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def clean_question(text):
    remove_words = ["My Account", "Practice", "Check", "Continue", "Submit"]
    for word in remove_words:
        text = text.replace(word, "")
    # Remove dynamic word counter lines to keep it stable
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        l_str = line.strip()
        if not l_str:
            continue
        if len(l_str) < 25 and l_str.lower().endswith("words") and any(char.isdigit() for char in l_str):
            continue
        cleaned_lines.append(l_str)
    return "\n".join(cleaned_lines).strip()

def get_answer(question):
    if question in cache:
        print("  [CACHE] Using cached answer...")
        return cache[question]

    print("  [API] Calling Groq...")
    system_prompt = """
You are a professional employee taking a highly graded business communication test.
Your task is to write a response that gets a PERFECT score.
Analyze the prompt carefully and fulfill every single constraint requested (e.g. if it asks to include a greeting, a clear situation, 2-3 key points, specific words, or particular sentence structures like 'and' or 'but', you MUST include them exactly).
Maintain a professional, natural tone, clear grammar, and correct spelling.
Do not add any preamble, conversational fluff, explanations, or quotes. Output ONLY the response text to be filled.
"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question}
        ]
    )
    answer = response.choices[0].message.content
    answer = answer.replace("**", "").replace("__", "").replace("##", "").replace("#", "")
    cache[question] = answer
    save_cache()
    return answer

def wait_and_click(page, role, name, timeout=15000):
    btn = page.get_by_role(role, name=name)
    btn.wait_for(state="visible", timeout=timeout)
    for _ in range(30):
        if btn.is_enabled():
            break
        time.sleep(0.1)
    time.sleep(0.1)
    btn.click()

def type_answer(element, answer):
    try:
        element.focus()
        element.fill(answer)
    except Exception as e:
        print(f"  [WARN] fill() failed on element: {e}. Trying evaluate fill...")
        try:
            element.evaluate("""(el, val) => {
                if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                    el.value = val;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    el.innerText = val;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }
            }""", answer)
        except Exception as eval_err:
            print(f"  [ERROR] JS fill failed: {eval_err}")

def answer_current_question(page_or_frame, q_num):
    page = page_or_frame
    print(f"\n{'='*55}")
    print(f"  [ACTIVE] {current_module_name} | Session Completed: {modules_completed}")
    print(f"  Question #{q_num}")

    # Wait to see if we are on a question page (either textarea, choice options, or a speaking/recording trigger)
    try:
        locator_str = "textarea:not([readonly]), [contenteditable='true'], div[role='textbox'], div.cursor-pointer, button:has-text('Record'), button:has-text('Speak'), div:has-text('Record'), div:has-text('Speak'), button:has-text('tap to speak'), div:has-text('tap to speak')"
        page.locator(locator_str).first.wait_for(state="visible", timeout=1000)
    except PlaywrightTimeout:
        pass

    time.sleep(0.2)

    # 1. Check if there is a speaking section (microphone or record button visible)
    speaking_btn = None
    speaking_btn_text = ""
    try:
        # Find and tag the speaking button in JS to prevent slow Python locator loop
        speaking_btn_text = page.evaluate(r"""() => {
            const findClickableByText = (keywords, exact) => {
                let tags = ['button', 'a', '[role="button"]', 'div', 'span'];
                for (let tag of tags) {
                    let elements = Array.from(document.querySelectorAll(tag));
                    for (let el of elements) {
                        let rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            let txt = (el.innerText || '').trim().toLowerCase();
                            let label = (el.getAttribute('title') || el.getAttribute('aria-label') || '').trim().toLowerCase();
                            if ((txt.length === 0 || txt.length > 25) && (label.length === 0 || label.length > 25)) continue;
                            let isMatch = false;
                            if (exact) {
                                isMatch = keywords.some(kw => (txt.length > 0 && txt === kw.toLowerCase()) || (label.length > 0 && label === kw.toLowerCase()));
                            } else {
                                isMatch = keywords.some(kw => (txt.length > 0 && txt.includes(kw.toLowerCase())) || (label.length > 0 && label.includes(kw.toLowerCase())));
                            }
                            if (isMatch) {
                                let hasClickableChild = el.querySelector('button, a, [role="button"]');
                                if (!hasClickableChild || tag === 'button' || tag === 'a') {
                                    return el;
                                }
                            }
                        }
                    }
                }
                return null;
            };

            let btn = findClickableByText(['start recording', 'record', 'speak', 'tap to speak', 'microphone'], false);
            if (btn) {
                btn.setAttribute('data-speaking-target', 'true');
                return (btn.innerText || btn.getAttribute('title') || btn.getAttribute('aria-label') || 'record').trim();
            }
            
            // Fallback for icons with title/aria-label containing record/speak
            let svgIcons = Array.from(document.querySelectorAll('[title*="Record"], [title*="Speak"], [aria-label*="Record"], [aria-label*="Speak"]'));
            for (let el of svgIcons) {
                let rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    el.setAttribute('data-speaking-target', 'true');
                    return el.getAttribute('title') || el.getAttribute('aria-label') || "Microphone Icon";
                }
            }
            return "";
        }""")
        if speaking_btn_text:
            speaking_btn = page.locator("[data-speaking-target='true']")
    except Exception:
        pass

    if speaking_btn:
        print(f"  [SPEAKING] Speaking question detected via button '{speaking_btn_text}'!")
        
        # Keep data-speaking-target tagged until recording is finished
        
        # Get page text context
        body = page.evaluate("document.body.innerText")
        page_context = clean_question(body)
        
        # Ask Groq to extract the text to read or generate the extempore response
        system_prompt = """
You are a workplace training assistant.
We have two types of speaking exercises on the page:
1. "Read Aloud" (or similar): The user is instructed to read a specific sentence or paragraph displayed on the page. In this case, extract and return ONLY the exact text to be read aloud.
2. "Extempore" / "Speaking Prompts" (e.g., "Express your View", "Self-Introduction", "Express your opinion", "Describe the image", "Speak about these points"): The user is given a prompt (like "Introduce yourself to a new colleague...") and must speak a brief response. In this case, write a brief, professional, and natural spoken response of 20 to 30 words answering the prompt, keeping it natural and conversational.

Analyze the page text carefully. If there is a specific text to read, return that text exactly. If it is an extempore/speaking prompt, generate a suitable response of 20-30 words (strictly under 180 characters including spaces so it works with TTS).
Return ONLY the final spoken text. Do not write any preamble, explanation, quotes, or markdown formatting.
"""
        print("  [API] Asking Groq for the text to read/generate...")
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": page_context}
            ]
        )
        text_to_speak = response.choices[0].message.content.strip().replace("**", "").replace("__", "").replace("##", "").replace("#", "").replace('"', '').replace("'", "")
        if not text_to_speak.strip():
            print("  [WARN] Generated text was empty. Using fallback response.")
            text_to_speak = "Hello, I am ready to answer the question."
        print(f"  [SPEAKING] Text to speak: '{text_to_speak}'")
        

        
        # Generate Audio using gTTS (Cross-platform)
        import base64
        import os
        from gtts import gTTS
        
        mp3_path = "temp_speech.mp3"
        b64_audio = ""
        try:
            tts = gTTS(text=text_to_speak, lang='en')
            tts.save(mp3_path)
            if os.path.exists(mp3_path):
                with open(mp3_path, "rb") as f:
                    b64_audio = base64.b64encode(f.read()).decode("utf-8")
                os.remove(mp3_path)
        except Exception as e:
            print(f"  [ERROR] Cross-platform gTTS generation failed: {e}")
            
        # Load TTS audio inside the browser
        if b64_audio:
            page.evaluate(f"window.speakBase64AudioIntoMic('{b64_audio}', 'audio/mpeg')")
            time.sleep(0.5)
        else:
            page.evaluate("(text) => window.speakTextIntoMic(text)", text_to_speak)
            time.sleep(1.5)
        
        # Click the Speak/Record button to start recording
        print("  [SPEAKING] Clicking Record button...")
        try:
            speaking_btn.click(timeout=5000)
        except Exception:
            page.evaluate("() => { if (document.querySelector('[data-speaking-target=\"true\"]')) document.querySelector('[data-speaking-target=\"true\"]').click(); }")
        
        # Wait 3.5 seconds for the 3-2-1 countdown animation to complete
        print("  [SPEAKING] Waiting 3.5 seconds for countdown animation to complete...")
        time.sleep(3.5)
        
        # Play the TTS audio into the fake mic stream
        print("  [SPEAKING] Injecting spoken audio into virtual microphone...")
        page.evaluate("window.startFakeMicPlayback()")
        
        # Wait for speaking duration (standard speaking rate is 2.5 words per second, minimum 6 seconds)
        words_count = len(text_to_speak.split())
        duration = max(6.0, float(words_count) / 2.5)
        total_record_wait = duration + 1.0
        print(f"  [SPEAKING] Waiting {total_record_wait:.1f} seconds for speech recording...")
        time.sleep(total_record_wait)
        
        # Check if the speaking button text has gone back to "Start Recording" or contains "Start" or "tap to speak"
        is_already_stopped = page.evaluate(r"""() => {
            let el = document.querySelector('[data-speaking-target="true"]');
            if (!el) return true;
            let txt = (el.innerText || '').trim().toLowerCase();
            let label = (el.getAttribute('title') || el.getAttribute('aria-label') || '').trim().toLowerCase();
            let hasStopWord = txt.includes('stop') || label.includes('stop');
            let hasRecordingWord = (txt.includes('recording') && !txt.includes('start')) || (label.includes('recording') && !label.includes('start'));
            if (hasStopWord || hasRecordingWord) {
                return false;
            }
            return true;
        }""")
        
        if is_already_stopped:
            print("  [SPEAKING] Recording stopped automatically.")
        else:
            # Check if we need to click the button again to stop recording
            # Find stop button inside page
            stop_btn_text = page.evaluate(r"""() => {
                const findClickableByText = (keywords, exact) => {
                    let tags = ['button', 'a', '[role="button"]', 'div', 'span'];
                    for (let tag of tags) {
                        let elements = Array.from(document.querySelectorAll(tag));
                        for (let el of elements) {
                            let rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                let txt = (el.innerText || '').trim().toLowerCase();
                                let label = (el.getAttribute('title') || el.getAttribute('aria-label') || '').trim().toLowerCase();
                                if ((txt.length === 0 || txt.length > 25) && (label.length === 0 || label.length > 25)) continue;
                                let isMatch = false;
                                if (exact) {
                                    isMatch = keywords.some(kw => (txt.length > 0 && txt === kw.toLowerCase()) || (label.length > 0 && label === kw.toLowerCase()));
                                } else {
                                    isMatch = keywords.some(kw => (txt.length > 0 && txt.includes(kw.toLowerCase())) || (label.length > 0 && label.includes(kw.toLowerCase())));
                                }
                                if (isMatch) {
                                    let hasClickableChild = el.querySelector('button, a, [role="button"]');
                                    if (!hasClickableChild || tag === 'button' || tag === 'a') {
                                        return el;
                                    }
                                }
                            }
                        }
                    }
                    return null;
                };

                let stopBtn = findClickableByText(['stop', 'stop recording', 'finish recording'], false);
                if (stopBtn) {
                    stopBtn.setAttribute('data-speaking-stop-target', 'true');
                    return (stopBtn.innerText || stopBtn.getAttribute('title') || stopBtn.getAttribute('aria-label') || 'stop').trim();
                }
                return "";
            }""")
            
            if stop_btn_text:
                print(f"  [SPEAKING] Clicking Stop recording button: '{stop_btn_text}'...")
                try:
                    page.locator("[data-speaking-stop-target='true']").click(timeout=3000)
                except Exception:
                    page.evaluate("() => { if (document.querySelector('[data-speaking-stop-target]')) document.querySelector('[data-speaking-stop-target]').click(); }")
                
                # Clean attribute
                try:
                    page.evaluate("() => { if (document.querySelector('[data-speaking-stop-target]')) document.querySelector('[data-speaking-stop-target]').removeAttribute('data-speaking-stop-target'); }")
                except Exception:
                    pass
            else:
                # Fallback to clicking the speaking button again if it's a toggle
                print("  [SPEAKING] No explicit stop button found. Clicking the record button again to toggle stop...")
                try:
                    speaking_btn.click(timeout=3000)
                except Exception:
                    page.evaluate("() => { if (document.querySelector('[data-speaking-target=\"true\"]')) document.querySelector('[data-speaking-target=\"true\"]').click(); }")
            time.sleep(1.0)
            
        print("  [SPEAKING] Speech recording complete!")
        # Clean up the temporary attribute from the DOM
        try:
            page.evaluate("() => { if (document.querySelector('[data-speaking-target]')) document.querySelector('[data-speaking-target]').removeAttribute('data-speaking-target'); }")
        except Exception:
            pass
        return True

    # 2. Check if there is a textbox/textarea (Writing section)
    textarea = page.locator("textarea:not([readonly]), [contenteditable='true'], div[role='textbox']").first
    is_textbox_visible = False
    try:
        if textarea.is_visible():
            is_textbox_visible = True
    except Exception:
        pass
        
    if False:  # is_textbox_visible: (disabled per user request)
        body = page.evaluate("""() => {
            let mainEl = document.querySelector('.main-content') || 
                         document.querySelector('main') || 
                         document.querySelector('.content') ||
                         document.querySelector('.app-content') ||
                         document.querySelector('.page-wrapper') ||
                         document.querySelector('.card-body') ||
                         document.body;
            
            let clone = mainEl.cloneNode(true);
            
            // Remove inputs, textareas, contenteditables, buttons, and bottom bars
            let inputs = clone.querySelectorAll('textarea, input, select, button, .bottom-bar, .buttons, [contenteditable="true"], div[role="textbox"]');
            for (let x of inputs) x.remove();
            
            let feed = clone.querySelectorAll('.writing-analysis, .feedback, .score, .result');
            for (let f of feed) f.remove();
            
            return clone.innerText;
        }""")
        question = clean_question(body)
        
        # Audio check
        transcript = get_audio_transcript(page)
        if transcript:
            question += f"\n\n[AUDIO TRANSCRIPT FROM LESSON]:\n{transcript}"

        print(f"  Q: {question[:300]}...")

        answer = get_answer(question)
        print(f"  A: {answer[:120]}...")

        type_answer(textarea, answer)
        return True

    # If no textarea, check for MCQ options (Reading/Listening section)
    options = []
    for retry_count in range(6):
        options = page.evaluate(r"""() => {
            let choices = [];
            let divs = Array.from(document.querySelectorAll('div'));
            for (let d of divs) {
                let classStr = d.getAttribute('class') || '';
                let txt = (d.innerText || '').trim();
                if (classStr.includes('cursor-pointer')) {
                    let firstWord = txt.split(/\s+/)[0] || '';
                    if (firstWord.match(/^\d+$/) && txt.length > 3 && txt.length < 250) {
                        let hasChildOption = Array.from(d.querySelectorAll('div')).some(child => {
                            let cCls = child.getAttribute('class') || '';
                            return cCls.includes('cursor-pointer');
                        });
                        if (!hasChildOption) {
                            choices.push({
                                text: txt,
                                index: parseInt(firstWord) - 1
                            });
                        }
                    }
                }
            }
            return choices;
        }""")
        if len(options) > 0:
            break
        print("  [WARN] Options not found, waiting 1s...")
        time.sleep(1)

    if len(options) > 0:
        print(f"  MCQ question detected with {len(options)} options:")
        for opt in options:
            print(f"    {opt['text']}")
            
        body = page.evaluate("""() => {
            let mainEl = document.querySelector('.main-content') || 
                         document.querySelector('main') || 
                         document.querySelector('.content') ||
                         document.querySelector('.app-content') ||
                         document.querySelector('.page-wrapper') ||
                         document.querySelector('.card-body') ||
                         document.body;
            
            let clone = mainEl.cloneNode(true);
            
            // Remove button bar and inputs to keep it clean
            let inputs = clone.querySelectorAll('button, .bottom-bar, .buttons');
            for (let x of inputs) x.remove();
            
            return clone.innerText;
        }""")
        question_context = clean_question(body)
        
        # Audio check
        transcript = get_audio_transcript(page)
        if transcript:
            question_context += f"\n\n[AUDIO TRANSCRIPT FROM LESSON]:\n{transcript}"
        
        system_prompt = """
You are a human student taking a reading comprehension or listening comprehension test.
Based on the passage, audio transcript, question, and choices provided on the page, select the correct option.
Return ONLY the number (1, 2, 3, or 4) of the correct choice.
Do not write anything else. Do not explain, return only the single digit.
"""
        user_prompt = f"""
Page Text & Audio Transcript Context:
{question_context}

Options to choose from:
{chr(10).join([opt['text'] for opt in options])}
"""
        print("  [API] Asking Groq for correct MCQ option...")
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )
        answer = response.choices[0].message.content.strip()
        print(f"  Groq returned: '{answer}'")
        
        chosen_number = None
        for char in answer:
            if char.isdigit():
                chosen_number = int(char)
                break
                
        if chosen_number is not None and 1 <= chosen_number <= len(options):
            option_text = options[chosen_number - 1]['text']
            print(f"  Clicking option #{chosen_number}: '{option_text}'")
            
            clicked = False
            try:
                # 1. Try fast whitespace-normalized JS click
                clicked = page.evaluate("""(optText) => {
                    const normalize = s => (s || '').replace(/\\s+/g, ' ').trim();
                    let target = normalize(optText);
                    
                    let divs = Array.from(document.querySelectorAll('div, button, span, p'));
                    for (let d of divs) {
                        if (normalize(d.innerText) === target) {
                            d.scrollIntoView({ block: 'center' });
                            d.click();
                            return true;
                        }
                    }
                    // Partial match fallback
                    for (let d of divs) {
                        let normText = normalize(d.innerText);
                        if (normText && target.includes(normText) && normText.length > 5) {
                            d.scrollIntoView({ block: 'center' });
                            d.click();
                            return true;
                        }
                    }
                    return false;
                }""", option_text)
                if clicked:
                    print("  ✓ Option clicked successfully via JS!")
            except Exception as e:
                print(f"  [WARN] JS click evaluation failed: {e}")

            if not clicked:
                # 2. Try Playwright click
                try:
                    normalized_text = " ".join(option_text.split())
                    opt_locator = page.locator("div.cursor-pointer, div, button").filter(has_text=normalized_text).first
                    opt_locator.click(timeout=2000)
                    clicked = True
                    print("  ✓ Option clicked via Playwright!")
                except Exception as e:
                    print(f"  [WARN] Playwright click failed: {e}")

            if not clicked:
                # 3. Try get_by_text fallback
                try:
                    page.get_by_text(option_text, exact=True).first.click(timeout=2000)
                    clicked = True
                    print("  ✓ Option clicked via get_by_text fallback!")
                except Exception as e:
                    print(f"  [ERROR] All click options failed: {e}")
                    
            time.sleep(0.2)
            return clicked
        else:
            print("  [ERROR] Invalid option number returned by LLM.")
            return False

    print("  [WARN] Neither textarea nor MCQ options were found.")
    return False

def detect_page_state(page):
    try:
        url = page.url.lower()
        if not url or url == "about:blank":
            return "home"
        if "/exam" in url or "/test" in url:
            return "exam"
        if "/lessons" in url:
            return "lessons"
        if "/units" in url:
            return "units"
        if "/grades" in url:
            return "grades"
        if "/practice" in url:
            return "practice"
        if "/home" in url:
            return "home"
    except Exception:
        pass
    return "unknown"

def click_check_button_if_any(page_or_frame):
    try:
        # Tag the target button in JS
        target_type = page_or_frame.evaluate("""() => {
            const getClickableAncestor = (el) => {
                let p = el;
                for (let i = 0; i < 4; i++) {
                    if (!p) break;
                    if (p.tagName === 'BUTTON' || p.tagName === 'A' || p.getAttribute('role') === 'button') {
                        return p;
                    }
                    p = p.parentElement;
                }
                return el;
            };

            const isElementDisabled = (el) => {
                let p = el;
                while (p) {
                    if (p.disabled || p.getAttribute('aria-disabled') === 'true') {
                        return true;
                    }
                    if (p.className && typeof p.className === 'string') {
                        if (p.className.toLowerCase().includes('disabled')) {
                            return true;
                        }
                    }
                    p = p.parentElement;
                }
                return false;
            };

            const findClickableByText = (keywords, exact) => {
                let tags = ['button', 'a', '[role="button"]', 'div', 'span'];
                for (let tag of tags) {
                    let elements = Array.from(document.querySelectorAll(tag));
                    for (let el of elements) {
                        let rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            let txt = (el.innerText || '').trim().toLowerCase();
                            let isMatch = false;
                            if (exact) {
                                isMatch = keywords.some(kw => txt === kw.toLowerCase());
                            } else {
                                isMatch = keywords.some(kw => txt.includes(kw.toLowerCase()));
                            }
                            if (isMatch) {
                                return getClickableAncestor(el);
                            }
                        }
                    }
                }
                return null;
            };

            let checkBtn = findClickableByText(['check'], true) || findClickableByText(['check'], false);
            if (checkBtn) {
                if (!isElementDisabled(checkBtn)) {
                    checkBtn.setAttribute('data-check-target', 'true');
                    return 'check';
                }
                return 'disabled';
            }
            
            let submitBtn = findClickableByText(['submit', 'finish'], true) || findClickableByText(['submit', 'finish'], false);
            if (submitBtn) {
                if (!isElementDisabled(submitBtn)) {
                    submitBtn.setAttribute('data-check-target', 'true');
                    return 'submit';
                }
                return 'disabled';
            }
            
            return 'not_found';
        }""")
        
        if target_type in ['check', 'submit']:
            # Native Playwright click!
            btn_loc = page_or_frame.locator("[data-check-target='true']")
            btn_loc.scroll_into_view_if_needed()
            btn_loc.click(timeout=3000)
            
            # Clean up attribute
            page_or_frame.evaluate("() => { if (document.querySelector('[data-check-target]')) document.querySelector('[data-check-target]').removeAttribute('data-check-target'); }")
            print(f"  [CHECK] Checked/Submitted question successfully via native click (Type: '{target_type}').")
            return True
    except Exception as e:
        print(f"  [WARN] Exception in click_check_button_if_any: {e}")
        # Make sure we clean up in case of failure
        try:
            page_or_frame.evaluate("() => { if (document.querySelector('[data-check-target]')) document.querySelector('[data-check-target]').removeAttribute('data-check-target'); }")
        except Exception:
            pass
    return False

def click_continue_button_if_any(page_or_frame):
    try:
        # Tag the target button in JS
        target_name = page_or_frame.evaluate("""() => {
            const getClickableAncestor = (el) => {
                let p = el;
                for (let i = 0; i < 4; i++) {
                    if (!p) break;
                    if (p.tagName === 'BUTTON' || p.tagName === 'A' || p.getAttribute('role') === 'button') {
                        return p;
                    }
                    p = p.parentElement;
                }
                return el;
            };

            const isElementDisabled = (el) => {
                let p = el;
                while (p) {
                    if (p.disabled || p.getAttribute('aria-disabled') === 'true') {
                        return true;
                    }
                    if (p.className && typeof p.className === 'string') {
                        if (p.className.toLowerCase().includes('disabled')) {
                            return true;
                        }
                    }
                    p = p.parentElement;
                }
                return false;
            };

            const findClickableByText = (keywords, exact) => {
                let tags = ['button', 'a', '[role="button"]', 'div', 'span'];
                for (let tag of tags) {
                    let elements = Array.from(document.querySelectorAll(tag));
                    for (let el of elements) {
                        let rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            let txt = (el.innerText || '').trim().toLowerCase();
                            let isMatch = false;
                            if (exact) {
                                isMatch = keywords.some(kw => txt === kw.toLowerCase());
                            } else {
                                isMatch = keywords.some(kw => txt.includes(kw.toLowerCase()));
                            }
                            if (isMatch) {
                                return getClickableAncestor(el);
                            }
                        }
                    }
                }
                return null;
            };

            let keywords = ["continue", "next", "finish", "next level", "unlock next level", "go to next level", "submit"];
            let target = findClickableByText(keywords, false);
            
            // Fallback: check for arrow icons or chevrons in HTML
            if (!target) {
                let buttons = Array.from(document.querySelectorAll('button, div, span, a'));
                for (let b of buttons) {
                    let rect = b.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        let html = b.innerHTML.toLowerCase();
                        let txt = (b.innerText || '').trim().toLowerCase();
                        if (html.includes('arrow') || html.includes('chevron') || html.includes('right') || html.includes('next')) {
                            if (b.tagName === 'BUTTON' || b.classList.contains('btn') || txt.length < 15) {
                                target = getClickableAncestor(b);
                                break;
                            }
                        }
                    }
                }
            }
            
            if (target) {
                if (!isElementDisabled(target)) {
                    target.setAttribute('data-continue-target', 'true');
                    return (target.innerText || target.getAttribute('aria-label') || 'continue').trim().toLowerCase();
                }
            }
            return null;
        }""")
        
        if target_name:
            # Native Playwright click!
            btn_loc = page_or_frame.locator("[data-continue-target='true']")
            btn_loc.scroll_into_view_if_needed()
            btn_loc.click(timeout=3000)
            
            # Clean up attribute
            page_or_frame.evaluate("() => { if (document.querySelector('[data-continue-target]')) document.querySelector('[data-continue-target]').removeAttribute('data-continue-target'); }")
            print(f"  ✓ Clicked transition button natively: '{target_name}'")
            return True
    except Exception as e:
        print(f"  [WARN] Exception in click_continue_button_if_any: {e}")
        # Clean up attribute in case of failure
        try:
            page_or_frame.evaluate("() => { if (document.querySelector('[data-continue-target]')) document.querySelector('[data-continue-target]').removeAttribute('data-continue-target'); }")
        except Exception:
            pass
    return False

def run_practice_main_handler(page):
    print("  [NAV] Currently on Practice main page. Scanning for Speaking card...")
    try:
        clicked = page.evaluate("""() => {
            // Find the exact element that says "Speaking"
            let elements = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, div, span, p'));
            let header = elements.find(el => (el.innerText || '').trim() === 'Speaking');
            if (header) {
                // Traverse up to find card container and find the button/link with "Start Learning"
                let parent = header;
                for (let depth = 0; depth < 5; depth++) {
                    if (!parent || parent === document.body) break;
                    let clickables = Array.from(parent.querySelectorAll('button, div, span, a'));
                    let btn = clickables.find(el => {
                        let txt = (el.innerText || '').trim().toLowerCase();
                        return txt.includes('start learning') || txt.includes('start') || txt.includes('learning');
                    });
                    if (btn) {
                        btn.scrollIntoView({ block: 'center' });
                        btn.click();
                        return true;
                    }
                    parent = parent.parentElement;
                }
            }
            return false;
        }""")
        if clicked:
            print("  ✓ Speaking card clicked!")
            time.sleep(1.5)
            return True
        else:
            print("  [WARN] Speaking card not found on main practice page.")
    except Exception as e:
        print(f"  [ERROR] run_practice_main_handler error: {e}")
    return False

def run_grades_handler(page):
    print("  [NAV] Currently on Level Journey page. Scanning for active level...")
    try:
        journey_clicked = page.evaluate("""() => {
            const findClickableByText = (keywords, exact) => {
                let tags = ['button', 'a', '[role="button"]', 'div', 'span'];
                for (let tag of tags) {
                    let elements = Array.from(document.querySelectorAll(tag));
                    for (let el of elements) {
                        let rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            let txt = (el.innerText || '').trim().toLowerCase();
                            let isMatch = false;
                            if (exact) {
                                isMatch = keywords.some(kw => txt === kw.toLowerCase());
                            } else {
                                isMatch = keywords.some(kw => txt.includes(kw.toLowerCase()));
                            }
                            if (isMatch) {
                                let hasClickableChild = el.querySelector('button, a, [role="button"]');
                                if (!hasClickableChild || tag === 'button' || tag === 'a') {
                                    return el;
                                }
                            }
                        }
                    }
                }
                return null;
            };

            let startBtn = findClickableByText(['start', 'resume'], true);
            if (startBtn) {
                startBtn.scrollIntoView({ block: 'center' });
                startBtn.click();
                return 'clicked_start';
            }
            
            let unlockBtn = findClickableByText(['unlock'], true);
            if (unlockBtn) {
                unlockBtn.scrollIntoView({ block: 'center' });
                unlockBtn.click();
                return 'clicked_unlock';
            }
            return 'none';
        }""")
        
        print(f"  [DEBUG] Level journey click action: {journey_clicked}")
        if journey_clicked == 'clicked_start':
            print("  ✓ Clicked Start/Resume level button!")
            time.sleep(1.5)
            return True
        elif journey_clicked == 'clicked_unlock':
            print("  [UNLOCK] Level Unlock clicked. Confirming modal...")
            time.sleep(0.5)
            modal_clicked = page.evaluate("""() => {
                let buttons = Array.from(document.querySelectorAll('button, div, span, a'));
                for (let mb of buttons) {
                    let rect = mb.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        let txt = (mb.innerText || '').trim().toLowerCase();
                        if (['confirm', 'yes', 'ok', 'unlock'].some(k => txt === k || txt.includes(k))) {
                            mb.click();
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if modal_clicked:
                print("  [UNLOCK] Modal confirmation clicked!")
            time.sleep(0.5)
            page.reload()
            time.sleep(1.5)
            return True
    except Exception as e:
        print(f"  [ERROR] run_grades_handler error: {e}")
    return False

def run_units_handler(page):
    print("  [NAV] Currently on Units page. Scanning for active or unlockable Units...")
    try:
        unit_clicked = page.evaluate("""() => {
            const findClickableByText = (keywords, exact) => {
                let tags = ['button', 'a', '[role="button"]', 'div', 'span'];
                for (let tag of tags) {
                    let elements = Array.from(document.querySelectorAll(tag));
                    for (let el of elements) {
                        let rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            let txt = (el.innerText || '').trim().toLowerCase();
                            let isMatch = false;
                            if (exact) {
                                isMatch = keywords.some(kw => txt === kw.toLowerCase());
                            } else {
                                isMatch = keywords.some(kw => txt.includes(kw.toLowerCase()));
                            }
                            if (isMatch) {
                                let hasClickableChild = el.querySelector('button, a, [role="button"]');
                                if (!hasClickableChild || tag === 'button' || tag === 'a') {
                                    return el;
                                }
                            }
                        }
                    }
                }
                return null;
            };

            // 1. Look for Start Learning or Continue Learning
            let startBtn = findClickableByText(['start learning', 'continue learning', 'resume learning'], false);
            if (startBtn) {
                startBtn.scrollIntoView({ block: 'center' });
                startBtn.click();
                return 'clicked_start';
            }
            
            // 2. Look for Unlock button
            let unlockBtn = findClickableByText(['unlock'], true);
            if (unlockBtn) {
                unlockBtn.scrollIntoView({ block: 'center' });
                unlockBtn.click();
                return 'clicked_unlock';
            }
            
            // 3. Look for Level Test
            let divs = Array.from(document.querySelectorAll('div'));
            let levelTestDiv = divs.find(d => {
                let txt = (d.innerText || '');
                return txt.includes('Level Test') && (txt.includes('Start') || txt.includes('Unlock'));
            });
            if (levelTestDiv) {
                let btn = levelTestDiv.querySelector('button, div, span, a');
                if (btn) {
                    btn.scrollIntoView({ block: 'center' });
                    btn.click();
                    let txt = (btn.innerText || '').trim().toLowerCase();
                    if (txt.includes('unlock')) {
                        return 'clicked_level_test_unlock';
                    }
                    return 'clicked_level_test_start';
                }
            }
            return 'none';
        }""")
        
        print(f"  [DEBUG] Unit click action: {unit_clicked}")
        if unit_clicked in ['clicked_start', 'clicked_level_test_start']:
            print("  ✓ Clicked Start Unit / Level Test button!")
            time.sleep(1.5)
            return True
        elif unit_clicked in ['clicked_unlock', 'clicked_level_test_unlock']:
            print("  [UNLOCK] Unit Unlock clicked. Confirming modal...")
            time.sleep(0.5)
            modal_clicked = page.evaluate("""() => {
                let buttons = Array.from(document.querySelectorAll('button, div, span, a'));
                for (let mb of buttons) {
                    let rect = mb.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        let txt = (mb.innerText || '').trim().toLowerCase();
                        if (['confirm', 'yes', 'ok', 'unlock'].some(k => txt === k || txt.includes(k))) {
                            mb.click();
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if modal_clicked:
                print("  [UNLOCK] Modal confirmation clicked!")
            time.sleep(0.5)
            page.reload()
            time.sleep(1.5)
            return True
    except Exception as e:
        print(f"  [ERROR] run_units_handler error: {e}")
    return False

def run_lessons_handler(page):
    print("  [NAV] Currently on Lessons page. Parsing cards...")
    try:
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1)
        
        lesson_action = page.evaluate(r"""() => {
            let cardElements = Array.from(document.querySelectorAll('.card, .card-body'));
            
            cardElements = cardElements.filter(div => {
                let text = div.innerText || '';
                if (!text.includes('Practice') && !text.includes('Unlock') && !text.includes('Video') && !text.includes('Learn')) {
                    return false;
                }
                let children = Array.from(div.querySelectorAll('.card, .card-body'));
                let hasChildCard = children.some(d => d !== div);
                return !hasChildCard;
            });
            
            let parsedCards = [];
            for (let card of cardElements) {
                let text = card.innerText || '';
                
                let title = '';
                let headings = card.querySelectorAll('h1, h2, h3, h4, h5, div, span, p');
                for (let h of headings) {
                    let hText = (h.innerText || '').trim();
                    if (hText.match(/^\d+\./)) {
                        title = hText;
                        break;
                    }
                }
                if (!title) {
                    let lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                    title = lines[0] || 'Unknown Lesson';
                }
                
                let pctMatch = text.match(/(\d+)%/);
                let percentage = pctMatch ? parseInt(pctMatch[1]) : 0;
                
                let isCompleted = percentage >= 70;
                
                let practiceBtn = card.querySelector('[title="Practice"]');
                if (!practiceBtn) {
                    let clickable = Array.from(card.querySelectorAll('button, div, span, a, svg, img'));
                    practiceBtn = clickable.find(el => {
                        let txt = (el.innerText || '').trim();
                        let titleAttr = el.getAttribute('title') || '';
                        let ariaAttr = el.getAttribute('aria-label') || '';
                        return txt === 'Practice' || titleAttr.includes('Practice') || ariaAttr.includes('Practice');
                    });
                }
                
                let unlockBtn = card.querySelector('[title="Unlock"]');
                if (!unlockBtn) {
                    let clickable = Array.from(card.querySelectorAll('button, div, span, a, svg, img'));
                    unlockBtn = clickable.find(el => {
                        let txt = (el.innerText || '').trim();
                        let titleAttr = el.getAttribute('title') || '';
                        let ariaAttr = el.getAttribute('aria-label') || '';
                        return txt.toLowerCase().includes('unlock') || titleAttr.toLowerCase().includes('unlock') || ariaAttr.toLowerCase().includes('unlock');
                    });
                }
                
                parsedCards.push({
                    title: title.trim(),
                    percentage: percentage,
                    isCompleted: isCompleted,
                    hasPracticeBtn: !!practiceBtn,
                    hasUnlockBtn: !!unlockBtn
                });
            }
            
            for (let idx = 0; idx < parsedCards.length; idx++) {
                let card = parsedCards[idx];
                if (!card.isCompleted) {
                    let domCard = cardElements[idx];
                    
                    let practiceBtn = domCard.querySelector('[title="Practice"]');
                    if (!practiceBtn) {
                        let clickable = Array.from(domCard.querySelectorAll('button, div, span, a, svg, img'));
                        practiceBtn = clickable.find(el => {
                            let txt = (el.innerText || '').trim();
                            let titleAttr = el.getAttribute('title') || '';
                            let ariaAttr = el.getAttribute('aria-label') || '';
                            return txt === 'Practice' || titleAttr.includes('Practice') || ariaAttr.includes('Practice');
                        });
                    }
                    
                    if (practiceBtn) {
                        practiceBtn.scrollIntoView({ block: 'center' });
                        practiceBtn.click();
                        return { action: 'clicked_practice', title: card.title };
                    }
                    
                    let unlockBtn = domCard.querySelector('[title="Unlock"]');
                    if (!unlockBtn) {
                        let clickable = Array.from(domCard.querySelectorAll('button, div, span, a, svg, img'));
                        unlockBtn = clickable.find(el => {
                            let txt = (el.innerText || '').trim();
                            let titleAttr = el.getAttribute('title') || '';
                            let ariaAttr = el.getAttribute('aria-label') || '';
                            return txt.toLowerCase().includes('unlock') || titleAttr.toLowerCase().includes('unlock') || ariaAttr.toLowerCase().includes('unlock');
                        });
                    }
                    
                    if (unlockBtn) {
                        unlockBtn.scrollIntoView({ block: 'center' });
                        unlockBtn.click();
                        return { action: 'clicked_unlock', title: card.title };
                    }
                }
            }
            
            if (parsedCards.length > 0) {
                return { action: 'all_completed' };
            }
            return { action: 'none' };
        }""")
        
        print(f"  [DEBUG] Lessons action taken: {lesson_action}")
        if lesson_action['action'] == 'clicked_practice':
            global current_module_name
            current_module_name = lesson_action['title']
            print(f"  [START] Clicked Practice for lesson '{current_module_name}'. Waiting for exam to load...")
            loaded = False
            for _ in range(15):
                if "/exam" in page.url or "/test" in page.url:
                    loaded = True
                    break
                time.sleep(0.3)
            if loaded:
                print("  ✓ Exam page loaded!")
                time.sleep(0.5)
            return True
            
        elif lesson_action['action'] == 'clicked_unlock':
            print(f"  [UNLOCK] Clicked unlock for lesson '{lesson_action['title']}'. Confirming modal...")
            time.sleep(0.5)
            modal_clicked = page.evaluate("""() => {
                let buttons = Array.from(document.querySelectorAll('button, div, span, a'));
                for (let mb of buttons) {
                    let rect = mb.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        let txt = (mb.innerText || '').trim().toLowerCase();
                        if (['confirm', 'yes', 'ok', 'unlock'].some(k => txt === k || txt.includes(k))) {
                            mb.click();
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if modal_clicked:
                print("  [UNLOCK] Modal confirmation clicked!")
            time.sleep(0.5)
            page.reload()
            time.sleep(1.5)
            return True
            
        elif lesson_action['action'] == 'all_completed':
            print("  [INFO] All lessons on this page are completed! Navigating back to Units page...")
            back_btn = page.locator("button[aria-label='Back']")
            if back_btn.is_visible() and back_btn.is_enabled():
                back_btn.click()
            else:
                page.go_back()
            time.sleep(1.5)
            page.reload()
            time.sleep(1.5)
            return True
            
        else:
            print("  [WARN] No actions taken on lessons page.")
    except Exception as e:
        print(f"  [ERROR] run_lessons_handler error: {e}")
    return False

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir="user_data",
        headless=False,
        permissions=["microphone"],
        args=[
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            "--disable-web-security",
            "--disable-site-isolation-trials",
            "--autoplay-policy=no-user-gesture-required"
        ]
    )
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.on("console", lambda msg: print(f"  [BROWSER] {msg.text}"))
        page.on("pageerror", lambda err: print(f"  [BROWSER ERROR] {err}"))
        
        # Inject fake microphone
        page.add_init_script("""
        if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
            const origEnumerateDevices = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
            navigator.mediaDevices.enumerateDevices = async function() {
                let devices = await origEnumerateDevices();
                let hasMic = devices.some(d => d.kind === 'audioinput');
                if (!hasMic) {
                    devices.push({
                        deviceId: "default",
                        kind: "audioinput",
                        label: "Fake Microphone",
                        groupId: "fake-group"
                    });
                }
                return devices;
            };
        }

        window.fakeMicAudioEl = null;
        window.fakeMicStream = null;
        
        window.initFakeMic = function() {
            if (window.fakeMicStream) {
                let tracks = window.fakeMicStream.getAudioTracks();
                let allActive = tracks.length > 0 && tracks.every(t => t.readyState === 'live');
                if (allActive) return;
                
                if (window.fakeMicAudioEl) {
                    try { window.fakeMicAudioEl.remove(); } catch(e) {}
                }
                window.fakeMicStream = null;
            }
            
            window.fakeMicAudioEl = document.createElement('audio');
            window.fakeMicAudioEl.crossOrigin = 'anonymous';
            (document.body || document.documentElement || document.head).appendChild(window.fakeMicAudioEl);
            
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            const audioCtx = new AudioContext();
            const dest = audioCtx.createMediaStreamDestination();
            const source = audioCtx.createMediaElementSource(window.fakeMicAudioEl);
            source.connect(dest);
            window.fakeMicStream = dest.stream;
            
            if (navigator.mediaDevices) {
                if (!window.origGetUserMedia) {
                    window.origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                }
                navigator.mediaDevices.getUserMedia = async function(constraints) {
                    if (constraints && constraints.audio && !constraints.video) {
                        window.initFakeMic();
                        console.log("[FAKE MIC] Returning fake audio stream");
                        return window.fakeMicStream;
                    }
                    return window.origGetUserMedia(constraints);
                };
            }
        };
        
        window.speakTextIntoMic = function(text) {
            window.initFakeMic();
            let url = "https://translate.google.com/translate_tts?ie=UTF-8&tl=en&client=tw-ob&q=" + encodeURIComponent(text);
            window.fakeMicAudioEl.src = url;
            window.fakeMicAudioEl.load();
            console.log("[FAKE MIC] Loaded TTS audio for: " + text);
        };
        
        window.speakBase64AudioIntoMic = function(b64Data, mimeType = "audio/mpeg") {
            window.initFakeMic();
            window.fakeMicAudioEl.removeAttribute('crossOrigin');
            window.fakeMicAudioEl.src = "data:" + mimeType + ";base64," + b64Data;
            window.fakeMicAudioEl.load();
            console.log("[FAKE MIC] Loaded Base64 audio stream (" + mimeType + ").");
        };
        
        window.startFakeMicPlayback = function() {
            if (window.fakeMicAudioEl) {
                window.fakeMicAudioEl.play();
                console.log("[FAKE MIC] Started playback");
            }
        };

        // Unblock copy-paste, context menu, and text selection
        const allowCopyPaste = function() {
            const events = ['copy', 'cut', 'paste', 'selectstart', 'contextmenu', 'dragstart'];
            events.forEach(eventName => {
                document.addEventListener(eventName, function(e) {
                    e.stopPropagation();
                }, true);
            });
            
            // Overwrite standard element level blocker properties
            document.addEventListener('readystatechange', function() {
                if (document.body) {
                    document.body.oncopy = null;
                    document.body.oncut = null;
                    document.body.onpaste = null;
                    document.body.onselectstart = null;
                    document.body.oncontextmenu = null;
                }
            });
            
            // CSS override for user-select
            const style = document.createElement('style');
            style.type = 'text/css';
            style.innerText = `
                * {
                    -webkit-user-select: text !important;
                    -moz-user-select: text !important;
                    -ms-user-select: text !important;
                    user-select: text !important;
                }
            `;
            document.head.appendChild(style);
        };
        
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', allowCopyPaste);
        } else {
            allowCopyPaste();
        }
        """)
        
        if not page.url or page.url == "about:blank":
            print("  Navigating to BET home page...")
            page.goto("https://corporate.bharatenglish.org/#/home")
            try:
                page.bring_to_front()
                import subprocess
                subprocess.run(["osascript", "-e", 'tell application "Google Chrome for Testing" to activate'], capture_output=True)
            except Exception:
                pass
        else:
            print(f"  Resuming on current page: {page.url}")
            try:
                page.bring_to_front()
                import subprocess
                subprocess.run(["osascript", "-e", 'tell application "Google Chrome for Testing" to activate'], capture_output=True)
            except Exception:
                print("\n" + "="*60)
        print("  BET AUTO-SOLVER — FULLY AUTOMATED (SPEAKING PREFERRED)")
        print("="*60)
        print("\n  Instructions:")
        print("  1. Browser mein login karein aur kisi bhi page par chalein.")
        print("  2. Script automatically current state detect karke solve aur navigate karegi.")
        print("  3. Press Ctrl+C in this terminal to exit.\n")
        
        last_question_id = None
        last_question_phase = "new"
        q_num = 1
        last_action_time = time.time()
        last_state = None
        
        print("\n  [ACTIVE] Browser window opened.")
        print("  - Please log in to the Bharat English Test portal in the browser.")
        input("  - After logging in, press ENTER here to start the automatic solver...")
        print("\n  [STARTING] Auto-solver is now active in the background. It will automatically detect and solve speaking questions as you navigate.")
        
        while True:
            try:
                # 1. Detect current page URL and state across all tabs
                active_page = page
                state = "home"
                for p in context.pages:
                    try:
                        p_state = detect_page_state(p)
                        if p_state == "exam":
                            active_page = p
                            state = "exam"
                            break
                        elif p_state in ["lessons", "units", "grades", "practice"]:
                            active_page = p
                            state = p_state
                    except Exception:
                        pass
                
                if state != last_state:
                    print(f"  [NAV] Browser state: '{state}'")
                    last_state = state
                    if state != "exam":
                        print("  Waiting for an exam/lesson page to be opened...")
                
                # Reset question trackers if we leave exam page
                if state != "exam":
                    if last_question_id is not None:
                        print("  [INFO] Left active exam. Resetting question trackers...")
                        last_question_id = None
                        last_question_phase = "new"
                        q_num = 1
                
                # 2. Find active frame for exam questions (if we are on an exam page)
                active_frame = None
                if state == "exam":
                    active_frame = find_active_question_frame(context)
                
                # 3. Route according to state
                if state == "exam":
                    if active_frame:
                        q_id = ""
                        try:
                            body = active_frame.evaluate("""() => {
                                let mainEl = document.querySelector('.main-content') || 
                                             document.querySelector('main') || 
                                             document.querySelector('.content') ||
                                             document.querySelector('.app-content') ||
                                             document.querySelector('.page-wrapper') ||
                                             document.querySelector('.card-body') ||
                                             document.body;
                                
                                console.log("[BODY DEBUG] Initial innerText length:", (mainEl.innerText || "").length);
                                let clone = mainEl.cloneNode(true);
                                
                                // Remove inputs, textareas, contenteditables, buttons, and bottom bars
                                let inputs = clone.querySelectorAll('textarea, input, select, button, .bottom-bar, .buttons, [contenteditable="true"], div[role="textbox"]');
                                for (let x of inputs) x.remove();
                                console.log("[BODY DEBUG] After inputs/buttons removal, length:", (clone.innerText || "").length);
                                
                                // Remove feedback, analysis, or dynamic score elements
                                let feed = clone.querySelectorAll('.writing-analysis, .feedback, .score, .result');
                                for (let f of feed) f.remove();
                                console.log("[BODY DEBUG] After feedback/score removal, length:", (clone.innerText || "").length);
                                
                                return clone.innerText;
                            }""")
                            print(f"  [DEBUG] Raw body representation: {repr(body)}")
                            q_id = clean_question(body)
                            print(f"  [DEBUG] Retrieved q_id length: {len(q_id)} | preview: {q_id[:80].replace(chr(10), ' ')}")
                        except Exception as eval_err:
                            print(f"  [ERROR] q_id evaluate exception: {eval_err}")

                        if q_id:
                            if q_id != last_question_id:
                                # New question detected!
                                print(f"\n[NEW QUESTION DETECTED] Question #{q_num}")
                                success = answer_current_question(active_frame, q_num)
                                if success:
                                    last_question_id = q_id
                                    last_question_phase = "answered"
                                    q_num += 1
                                    last_action_time = time.time()
                                else:
                                    print("  [WARN] Failed to answer question. Retrying in 0.5s...")
                                    time.sleep(0.5)
                            else:
                                if ASSIST_MODE:
                                    time.sleep(0.2)
                                    continue
                                    
                                # Same question. Check current phase
                                elapsed = time.time() - last_action_time
                                if elapsed > 45.0:
                                    print(f"  [WARN] Stuck on the same question for {elapsed:.1f}s. Reloading page...")
                                    page.reload()
                                    time.sleep(3)
                                    last_question_id = None
                                    last_question_phase = "new"
                                    last_action_time = time.time()
                                    continue
                                    
                                if last_question_phase == "answered":
                                    if int(elapsed) % 3 == 0:
                                        print(f"  [EXAM] Question is answered. Waiting for 'Check' button (elapsed: {elapsed:.1f}s)...")
                                    check_clicked = click_check_button_if_any(active_frame)
                                    if check_clicked:
                                        last_question_phase = "checked"
                                        last_action_time = time.time()
                                        time.sleep(0.3)
                                    else:
                                        time.sleep(0.3)
                                elif last_question_phase == "checked":
                                    if int(elapsed) % 3 == 0:
                                        print(f"  [EXAM] Question is checked. Waiting for 'Continue' button (elapsed: {elapsed:.1f}s)...")
                                    continue_clicked = click_continue_button_if_any(active_frame)
                                    if continue_clicked:
                                        # Let next question load
                                        last_action_time = time.time()
                                        time.sleep(0.5)
                                    else:
                                        time.sleep(0.3)
                        else:
                            if not ASSIST_MODE:
                                # Question text not retrieved, try to continue/submit anyway
                                print("  [EXAM] Empty question ID. Checking for transition buttons...")
                                click_continue_button_if_any(active_frame)
                            time.sleep(0.5)
                    else:
                        # No active question frame detected (loading page or result card)
                        if not ASSIST_MODE:
                            print("  [EXAM] No active question frame detected (loading or finished). Scanning buttons...")
                            clicked_btn = click_continue_button_if_any(active_page)
                        else:
                            time.sleep(0.5)

                            
                else:
                    time.sleep(0.5)
                    
            except KeyboardInterrupt:
                print("\n  Exiting solver...")
                break
            except Exception as e:
                print(f"  [ERROR] Loop error: {e}")
                time.sleep(0.5)
    finally:
        print("  Closing browser context to release profile lock...")
        try:
            context.close()
        except Exception:
            pass
