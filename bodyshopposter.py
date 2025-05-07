# Pipedream Python Code Step
import requests
import os
import time
from openai import OpenAI # Using the library directly

# === Accessing Environment Variables (Pipedream injects these) ===
ARENA_TOKEN = os.environ.get("ARENA_TOKEN")
ARENA_CHANNEL_SLUG = os.environ.get("ARENA_CHANNEL_SLUG")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SCREENSHOT_API_KEY = os.environ.get("SCREENSHOT_API_KEY") # Optional

if not all([ARENA_TOKEN, ARENA_CHANNEL_SLUG, OPENAI_API_KEY]):
    raise Exception("Missing required environment variables: ARENA_TOKEN, ARENA_CHANNEL_SLUG, OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)
DATA_STORE_KEY_PROCESSED_IDS = f"processed_arena_block_ids_{ARENA_CHANNEL_SLUG}"

def get_processed_block_ids(pd):
    if "processed_items_store" not in pd.inputs:
        print("WARNING: Pipedream Data Store named 'processed_items_store' not connected. Using temporary in-memory store for this run.")
        if not hasattr(get_processed_block_ids, "_temp_store"):
            get_processed_block_ids._temp_store = set()
        return get_processed_block_ids._temp_store
    
    data_store = pd.inputs["processed_items_store"]
    processed_ids_str = data_store.get(DATA_STORE_KEY_PROCESSED_IDS)
    if processed_ids_str:
        return set(processed_ids_str.split(','))
    return set()

def add_processed_block_id(pd, block_id, current_set):
    if "processed_items_store" not in pd.inputs:
        current_set.add(str(block_id))
        print(f"INFO: Block {block_id} marked as processed (in-memory for this run).")
        return

    data_store = pd.inputs["processed_items_store"]
    current_set.add(str(block_id))
    ids_to_store = list(current_set)
    if len(",".join(ids_to_store)) > 200000: 
        ids_to_store = ids_to_store[-1000:] 
    
    data_store.set(DATA_STORE_KEY_PROCESSED_IDS, ",".join(ids_to_store))
    print(f"INFO: Block {block_id} added to processed_items_store.")


def get_description_from_vision_api(image_url=None, prompt_text="Describe this for a tweet."):
    if not image_url:
        return "No image provided for description."
    try:
        response = client.chat.completions.create(
            model="gpt-4-vision-preview", 
            messages=[
                {"role": "user", "content": [{"type": "text", "text": prompt_text}, {"type": "image_url", "image_url": {"url": image_url}}]}
            ],
            max_tokens=120 # Slightly increased for potentially more detailed descriptions
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error calling OpenAI Vision API for {image_url}: {e}")
        return "AI description unavailable."

def take_screenshot_and_get_url(webpage_url):
    if not SCREENSHOT_API_KEY:
        print("SCREENSHOT_API_KEY not set. Cannot take screenshot. Returning placeholder.")
        return "https://via.placeholder.com/600x400.png?text=Webpage+Screenshot+Missing+API+Key" 
    
    api_url = "https://api.screenshotapi.net/capture" # Example, use your actual API
    params = {'token': SCREENSHOT_API_KEY, 'url': webpage_url, 'width': 1200, 'height': 800, 'output': 'json', 'file_type': 'png', 'ttl': 86400 }
    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        screenshot_url = response.json().get('screenshot')
        print(f"Screenshot successful for {webpage_url}: {screenshot_url}")
        return screenshot_url
    except Exception as e:
        print(f"Screenshot failed for {webpage_url}: {e}")
        return "https://via.placeholder.com/600x400.png?text=Screenshot+Failed"


def handler(pd, steps): 
    processed_block_ids = get_processed_block_ids(pd)
    headers = {"Authorization": f"Bearer {ARENA_TOKEN}"}
    
    print(f"Checking Are.na channel: {ARENA_CHANNEL_SLUG} for new blocks. Already processed: {len(processed_block_ids)} blocks.")

    try:
        response = requests.get(
            f"https://api.are.na/v2/channels/{ARENA_CHANNEL_SLUG}/contents?per=10&sort=position&direction=desc",
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        channel_data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Are.na channel: {e}")
        return {"error": f"Error fetching Are.na channel: {e}"}

    new_items_to_tweet = []
    current_processed_set = set(processed_block_ids) 

    for block in reversed(channel_data.get("contents", [])): 
        block_id = str(block['id'])
        if block_id in current_processed_set:
            continue

        print(f"\nProcessing new block: {block.get('title', 'Untitled')} (ID: {block_id}, Class: {block['class']})")

        block_title = block.get('title') or "Are.na Find"
        arena_block_url = f"https://www.are.na/block/{block_id}"
        
        ai_description = ""
        media_url_for_twitter = None
        link_in_text = arena_block_url # Default link

        # === UPDATED PROMPT LOGIC ===
        if block['class'] == "Image":
            media_url_for_twitter = block.get('image', {}).get('original', {}).get('url')
            if media_url_for_twitter:
                ai_prompt = (
                    "Provide a short, engaging description of this image, suitable for a tweet (under 150 characters if possible). "
                    "If the image features a recognizable vehicle, please identify its make and model (e.g., 'Audi A4', 'Ford Mustang Mach-E'). "
                    "If it's another specific product, artwork, or landmark, name it if clearly identifiable. "
                    "The image will be attached to the tweet."
                )
                ai_description = get_description_from_vision_api(image_url=media_url_for_twitter, prompt_text=ai_prompt)
            else:
                ai_description = "Image content." # Generic if URL is missing
        
        elif block['class'] == "Link":
            source_url = block.get('source', {}).get('url')
            if source_url:
                link_in_text = source_url # Prefer original source link for links
                if SCREENSHOT_API_KEY: 
                    screenshot_url = take_screenshot_and_get_url(source_url)
                    if screenshot_url and "placeholder" not in screenshot_url and "Failed" not in screenshot_url :
                        media_url_for_twitter = screenshot_url
                        ai_prompt = (
                            "Based on this webpage screenshot, write a very concise summary (1-2 sentences) of its main topic for a tweet. "
                            "If it clearly shows a specific product, article title, or brand, mention it. "
                            "The screenshot will be attached."
                        )
                        ai_description = get_description_from_vision_api(image_url=media_url_for_twitter, prompt_text=ai_prompt)
                    else: # Screenshot failed or placeholder
                        ai_description = f"{block.get('generated_title', '') or 'Interesting Link'}"
                else: # No screenshot API key
                     ai_description = f"{block.get('generated_title', '') or 'Web Link'}"
            else:
                ai_description = "Link content." # Generic if URL is missing
        
        elif block['class'] == "Text":
            text_content = block.get('content', '')
            # from bs4 import BeautifulSoup # If you use this, add 'beautifulsoup4' to requirements in Pipedream
            # soup = BeautifulSoup(block.get('content_html', ''), 'html.parser')
            # text_content = soup.get_text()
            if len(text_content) > 150:
                ai_description = text_content[:147] + "..."
            elif text_content:
                ai_description = text_content
            else:
                ai_description = "Text content."
        
        else:
            print(f"Unsupported block type: {block['class']}. Skipping.")
            add_processed_block_id(pd, block_id, current_processed_set)
            continue

        # Compose Tweet Text
        # Ensure title is not None before prepending
        title_prefix = f"{block_title}: " if block_title else ""
        tweet_parts = [f"{title_prefix}{ai_description}"]
        
        # Add link to the text. Twitter handles URL shortening.
        # Always include a link, either to original source or Are.na block
        tweet_parts.append(link_in_text) 
        
        composed_tweet_text = " ".join(tweet_parts)
        
        # Max length for tweets is 280. t.co links are 23 chars.
        # So, text + 1 space + 23 chars for link <= 280
        # Max text length = 280 - 1 - 23 = 256
        # Let's be a bit more conservative: 250 for text part.
        
        # Reconstruct if too long, prioritizing title and AI description
        temp_text_for_length_check = f"{title_prefix}{ai_description}"
        if len(temp_text_for_length_check) > 250:
            available_chars_for_desc = 250 - len(title_prefix) - 3 # -3 for "..."
            if available_chars_for_desc > 20 : # Ensure there's meaningful space for description
                ai_description_trimmed = ai_description[:available_chars_for_desc] + "..."
                composed_tweet_text = f"{title_prefix}{ai_description_trimmed} {link_in_text}"
            else: # Not enough space for a good description, just use title and link
                composed_tweet_text = f"{title_prefix_shortened_if_needed} {link_in_text}" # You might need to shorten title too
        
        # Final check, should be rare now
        if len(composed_tweet_text) > 280:
             composed_tweet_text = composed_tweet_text[:277] + "..."


        new_items_to_tweet.append({
            "tweet_text": composed_tweet_text,
            "media_url": media_url_for_twitter,
            "block_id_to_mark_processed": block_id
        })
        
        # time.sleep(1) # Small delay if processing many in one go

    if new_items_to_tweet:
        for item in new_items_to_tweet:
            add_processed_block_id(pd, item["block_id_to_mark_processed"], current_processed_set)
            # Exporting the item for the next Pipedream step
            # The `return` here in Pipedream's Python step context actually means exporting data for the next step
            # if multiple items are found, this will create multiple downstream executions.
            yield item # Use yield to emit multiple events if multiple new blocks are found
        print(f"Yielded {len(new_items_to_tweet)} items to be tweeted.")
    else:
        print("No new blocks to tweet.")
    
    # A summary return for the overall step execution log, not for individual tweets
    # This return value is what shows up as the "result" of the Python step itself.
    # The `yield` statements above are what trigger subsequent steps for each tweet.
    return {"summary": f"Processed Are.na blocks. Yielded {len(new_items_to_tweet)} new items for tweeting."}