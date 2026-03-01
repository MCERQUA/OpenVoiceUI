/**
 * AI helper using Google Gemini
 */

import { GoogleGenAI } from '@google/genai';

const genAI = new GoogleGenAI(process.env.GEMINI_API_KEY);

/**
 * Generate post variations
 */
export async function generatePostVariations(topic, brandContext, count = 3) {
  const model = genAI.getGenerativeModel({ model: 'gemini-2.0-flash' });

  const prompt = `You are a social media content creator for ${brandContext.name || 'a business'}.

Brand context:
${brandContext.profile || 'Professional services company'}

Topic: ${topic}

Generate ${count} different post variations:
1. Educational - teach something valuable
2. Urgency - create a sense of urgency
3. Solution-focused - present a solution to a problem

Each post should be:
- 280 characters max
- Include 2-3 relevant hashtags
- Engaging and on-brand

Format as JSON array:
[{"type": "educational", "content": "...", "hashtags": ["#tag1", "#tag2"]}, ...]`;

  try {
    const result = await model.generateContent(prompt);
    const text = result.response.text();

    // Extract JSON from response
    const jsonMatch = text.match(/\[[\s\S]*\]/);
    if (jsonMatch) {
      return JSON.parse(jsonMatch[0]);
    }

    // Fallback parsing
    return [
      { type: 'general', content: topic, hashtags: [] }
    ];
  } catch (error) {
    console.error('AI generation error:', error);
    throw new Error('Failed to generate post variations');
  }
}

/**
 * Generate image prompt for a post
 */
export async function generateImagePrompt(postContent, brandContext) {
  const model = genAI.getGenerativeModel({ model: 'gemini-2.0-flash' });

  const prompt = `Create a detailed image generation prompt for a social media post.

Brand: ${brandContext.name || 'Company'}
Brand colors: ${brandContext.colors || '#4a9eff, #2ecc71'}
Post content: ${postContent}

Requirements:
- Professional and eye-catching
- Include brand colors subtly
- Suitable for Instagram/Facebook
- No text overlays

Output just the image prompt (max 200 characters):`;

  try {
    const result = await model.generateContent(prompt);
    return result.response.text().trim();
  } catch (error) {
    console.error('Image prompt generation error:', error);
    return `Professional social media image for ${brandContext.name || 'business'} with ${brandContext.colors || 'blue'} accents`;
  }
}

/**
 * Optimize blog title for SEO
 */
export async function optimizeTitle(title, keywords = []) {
  const model = genAI.getGenerativeModel({ model: 'gemini-2.0-flash' });

  const prompt = `Optimize this blog title for SEO.

Original title: ${title}
Target keywords: ${keywords.join(', ') || 'none provided'}

Requirements:
- Keep it under 60 characters
- Include primary keyword naturally
- Make it compelling for clicks

Output just the optimized title:`;

  try {
    const result = await model.generateContent(prompt);
    return result.response.text().trim();
  } catch (error) {
    console.error('Title optimization error:', error);
    return title;
  }
}

export default { generatePostVariations, generateImagePrompt, optimizeTitle };
