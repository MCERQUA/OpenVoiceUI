/**
 * AI helper using Google Gemini
 * Falls back to mock mode if GEMINI_API_KEY not set
 */

const HAS_API_KEY = !!process.env.GEMINI_API_KEY;

// Only import and create client if API key is set
let ai = null;
if (HAS_API_KEY) {
  const { GoogleGenAI } = await import('@google/genai');
  ai = new GoogleGenAI(process.env.GEMINI_API_KEY);
}

/**
 * Check if AI is available
 */
export function hasAI() {
  return HAS_API_KEY;
}

/**
 * Generate text content
 */
export async function generateText(prompt, options = {}) {
  if (!HAS_API_KEY) {
    console.warn('Gemini API not configured - returning mock response');
    return {
      text: 'AI generation not available. Please configure GEMINI_API_KEY.',
      success: false
    };
  }

  try {
    const response = await ai.models.generateContent({
      model: options.model || 'gemini-2.0-flash',
      contents: prompt,
      ...options
    });
    
    return {
      text: response.text,
      success: true
    };
  } catch (error) {
    console.error('Gemini error:', error);
    return {
      text: `Error: ${error.message}`,
      success: false
    };
  }
}

/**
 * Generate image prompt for post
 */
export async function generateImagePrompt(postContent, brandContext) {
  if (!HAS_API_KEY) {
    return {
      prompt: 'AI image generation not available',
      success: false
    };
  }

  const prompt = `Create an image prompt for a social media post.

Brand: ${brandContext?.name || 'Unknown'}
Post content: ${postContent}

Generate a detailed image prompt that:
1. Matches the brand's visual style
2. Is appropriate for social media
3. Includes relevant visual elements
4. Specifies style, lighting, and composition

Return only the image prompt text.`;

  const response = await generateText(prompt);
  return {
    prompt: response.text,
    success: response.success
  };
}

/**
 * Optimize a title for SEO
 */
export async function optimizeTitle(title, context = '') {
  if (!HAS_API_KEY) {
    return {
      optimizedTitle: title,
      success: false
    };
  }

  const prompt = `Optimize this title for SEO and engagement.

Original title: ${title}
Context: ${context}

Return an optimized title that:
1. Is under 60 characters
2. Includes relevant keywords
3. Is engaging and click-worthy
4. Maintains the original meaning

Return only the optimized title.`;

  const response = await generateText(prompt);
  return {
    optimizedTitle: response.text.trim().replace(/^["']|["']$/g, ''),
    success: response.success
  };
}

/**
 * Generate post variations for a brand
 */
export async function generatePostVariations(brandContext, topic, count = 3) {
  if (!HAS_API_KEY) {
    return [{
      title: 'Demo Post',
      content: 'AI generation not available. Please configure GEMINI_API_KEY.',
      style: 'educational'
    }];
  }

  const prompt = `Generate ${count} social media post variations for a brand.

Brand context: ${brandContext}
Topic: ${topic}

Generate ${count} posts with different styles:
1. Educational - teach something valuable
2. Urgency - create urgency or fear of missing out
3. Solution-focused - present the brand as a solution

Format as JSON array with title, content, and style fields.`;

  const response = await generateText(prompt);
  
  try {
    return JSON.parse(response.text);
  } catch {
    return [{
      title: 'Generated Post',
      content: response.text,
      style: 'general'
    }];
  }
}

export default { hasAI, generateText, generateImagePrompt, optimizeTitle, generatePostVariations };
