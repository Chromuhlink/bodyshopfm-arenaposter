```javascript
// n8n Code Node: Process Are.na and OpenAI

// --- Configuration (Set these as n8n Environment Variables if possible, or hardcode for initial test) ---
const ARENA_TOKEN = $env.ARENA_TOKEN || "YOUR_ARENA_TOKEN_HERE"; // Replace or use n8n env
const ARENA_CHANNEL_SLUG = $env.ARENA_CHANNEL_SLUG || "YOUR_CHANNEL_SLUG_HERE"; // Replace or use n8n env
const OPENAI_API_KEY = $env.OPENAI_API_KEY || "YOUR_OPENAI_API_KEY_HERE"; // Replace or use n8n env
const SCREENSHOT_API_KEY = $env.SCREENSHOT_API_KEY || null; // Optional, replace or use n8n env

// --- Simple In-Workflow State for Processed IDs (using staticData) ---
// To reset for testing: Manually edit workflow, clear staticData.processedBlockIds_v2, save.
const PROCESSED_IDS_KEY = 'processedBlockIds_v2'; // Use a distinct key
if (!this.getWorkflowStaticData('global')[PROCESSED_IDS_KEY]) {
  this.getWorkflowStaticData('global')[PROCESSED_IDS_KEY] = [];
}
let processedBlockIds = this.getWorkflowStaticData('global')[PROCESSED_IDS_KEY];
// Keep only the last N IDs to prevent staticData from growing too large
const MAX_PROCESSED_IDS = 200; 
if (processedBlockIds.length > MAX_PROCESSED_IDS) {
    processedBlockIds = processedBlockIds.slice(-MAX_PROCESSED_IDS);
    this.getWorkflowStaticData('global')[PROCESSED_IDS_KEY] = processedBlockIds;
}


async function getArenaBlocks() {
  const url = `https://api.are.na/v2/channels/${ARENA_CHANNEL_SLUG}/contents?per=10&sort=position&direction=desc`;
  const options = {
    headers: { 'Authorization': `Bearer ${ARENA_TOKEN}` },
    json: true, // n8n's internal HTTP client often handles JSON parsing
  };
  try {
    // Using n8n's built-in HTTP request capabilities for simplicity within a Code node.
    // For more complex HTTP needs, use a separate HTTP Request Node.
    const responseData = await this.helpers.httpRequest(options, url);
    return responseData.contents || [];
  } catch (error) {
    console.error("Error fetching Are.na blocks:", error.message);
    // To stop workflow execution from code node on error:
    // throw new Error("Failed to fetch Are.na blocks: " + error.message);
    return [];
  }
}

async function getOpenAIDescription(imageUrl, prompt) {
  if (!OPENAI_API_KEY) {
    console.warn("OpenAI API Key not set.");
    return "AI description unavailable (no API key).";
  }
  const url = "https://api.openai.com/v1/chat/completions";
  const body = {
    model: "gpt-4-vision-preview",
    messages: [{
      role: "user",
      content: [
        { type: "text", text: prompt },
        { type: "image_url", image_url: { url: imageUrl } }
      ]
    }],
    max_tokens: 120
  };
  const options = {
    headers: {
      'Authorization': `Bearer ${OPENAI_API_KEY}`,
      'Content-Type': 'application/json'
    },
    body: body,
    method: 'POST',
    json: true,
  };
  try {
    const responseData = await this.helpers.httpRequest(options, url);
    return responseData.choices[0].message.content.trim();
  } catch (error) {
    console.error("Error calling OpenAI:", error.message);
    return "AI description currently unavailable.";
  }
}

async function takeScreenshot(webpageUrl) {
    if (!SCREENSHOT_API_KEY) {
        console.log("Screenshot API Key not provided. Skipping screenshot.");
        return null;
    }
    // Replace with your actual Screenshot API details
    const screenshotApiUrl = `https://api.screenshotapi.net/capture?token=${SCREENSHOT_API_KEY}&url=${encodeURIComponent(webpageUrl)}&width=1200&height=800&output=json&file_type=png&ttl=86400`;
    try {
        const responseData = await this.helpers.httpRequest({json: true}, screenshotApiUrl);
        return responseData.screenshot;
    } catch (error) {
        console.error("Screenshot failed:", error.message);
        return null;
    }
}


// --- Main Logic ---
const arenaBlocks = await getArenaBlocks();
let newBlockToProcess = null;

for (const block of arenaBlocks.slice().reverse()) { // Process oldest first
  const blockId = String(block.id);
  if (!processedBlockIds.includes(blockId)) {
    newBlockToProcess = block;
    break; // Process only one new block per run for simplicity
  }
}

if (!newBlockToProcess) {
  console.log("No new Are.na blocks to process in this run.");
  return []; // Must return an array for n8n items
}

console.log(`Processing new block: ID ${newBlockToProcess.id}, Title: ${newBlockToProcess.title || 'Untitled'}`);

const block = newBlockToProcess;
const blockTitle = block.title || "Are.na Discovery";
const arenaBlockUrl = `https://www.are.na/block/${block.id}`;
let linkInText = arenaBlockUrl;
let mediaUrlForTwitter = null;
let aiDescription = "";

if (block.class === "Image" && block.image && block.image.original && block.image.original.url) {
  mediaUrlForTwitter = block.image.original.url;
  const prompt = "Short, engaging description for this image (for a tweet, under 150 chars). If it's a vehicle, try to ID make/model. The image will be attached.";
  aiDescription = await getOpenAIDescription(mediaUrlForTwitter, prompt);
} else if (block.class === "Link" && block.source && block.source.url) {
  linkInText = block.source.url;
  if (SCREENSHOT_API_KEY) {
      const screenshotUrl = await takeScreenshot(block.source.url);
      if (screenshotUrl) {
          mediaUrlForTwitter = screenshotUrl;
          const prompt = "Concise summary of this webpage screenshot (for a tweet, 1-2 sentences). Mention products/brands if clear. Screenshot attached.";
          aiDescription = await getOpenAIDescription(mediaUrlForTwitter, prompt);
      } else {
          aiDescription = block.generated_title || "Interesting Link";
      }
  } else {
    aiDescription = block.generated_title || "Web Link";
  }
} else if (block.class === "Text") {
  aiDescription = block.content || "Text content.";
  if (aiDescription.length > 150) aiDescription = aiDescription.substring(0, 147) + "...";
} else {
  aiDescription = "New content from Are.na."; // Fallback for other types
}

// Compose tweet
const titlePrefix = blockTitle ? `${blockTitle}: ` : "";
let tweetText = `${titlePrefix}${aiDescription} ${linkInText}`;
if (tweetText.length > 280) { // Basic trim
    tweetText = tweetText.substring(0, 277) + "...";
}

// Mark as processed *before* attempting to tweet for this simplified model
processedBlockIds.push(String(block.id));
this.getWorkflowStaticData('global')[PROCESSED_IDS_KEY] = processedBlockIds; // Save updated list

// Output for the Twitter node
// n8n expects an array of items. Each item usually has a 'json' property.
return [{
  json: { // This structure makes it easy to reference in the next node as $json.tweet_text
    tweet_text: tweetText,
    media_url: mediaUrlForTwitter // Twitter node will use this if present
  }
}];
```
*   **Explanation of Simplifications:**
*   **One Block Per Run:** The code finds the oldest new block and processes only that one. This is to be extremely conservative with API rate limits (especially Twitter's free tier).
*   **State Management:** Uses `this.getWorkflowStaticData('global')` which persists data *with the workflow definition*. It's simple but not as robust as an external DB. For testing, you might need to manually clear `staticData.processedBlockIds_v2` in the workflow settings if you want to re-process blocks.
*   **HTTP Requests in Code Node:** Uses `this.helpers.httpRequest` for API calls directly within the Code node. This keeps the workflow visually simpler (fewer nodes) but makes debugging API calls slightly harder than with dedicated HTTP Request nodes.
*   **Error Handling:** Basic `console.error` and fallbacks. Production workflows would need more robust error handling.
*   **n8n Environment Variables:**
*   Go to your n8n instance "Settings" -> "Environment Variables" (if self-hosted) or workflow settings if available in n8n Cloud.
*   Define `ARENA_TOKEN`, `ARENA_CHANNEL_SLUG`, `OPENAI_API_KEY`, `SCREENSHOT_API_KEY`.
*   If you don't set them as global n8n env vars, you'll need to hardcode them in the script (less secure, okay for initial testing).