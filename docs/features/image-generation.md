---
sidebar_position: 4
title: Image Generation
---

# Image Generation

OpenVoiceUI generates images server-side using Google Gemini and HuggingFace Inference API. API keys stay on the server — the browser never sees them. All generated images are saved to the server immediately.

## Supported Providers

### Google Gemini (default)

- **Model:** `nano-banana-pro-preview`
- **Supports:** text-to-image and image-to-image (edit existing images)
- **Config:** `GEMINI_API_KEY` in `.env`

### HuggingFace Inference API

| Model | ID |
|-------|-----|
| FLUX.1 Schnell | `black-forest-labs/FLUX.1-schnell` |
| FLUX.1 Dev | `black-forest-labs/FLUX.1-dev` |
| Stable Diffusion XL | `stabilityai/stable-diffusion-xl-base-1.0` |
| Stable Diffusion 3.5 Large | `stabilityai/stable-diffusion-3.5-large` |
| SD 3.5 Large Turbo | `stabilityai/stable-diffusion-3.5-large-turbo` |

- **Config:** `HF_TOKEN` in `.env`
- **Prefix:** Models are selected with `hf:` prefix (e.g., `hf:black-forest-labs/FLUX.1-schnell`)

## Quality Tiers

| Quality | Resolution |
|---------|-----------|
| `standard` | 1024 x 1024 |
| `high` | 1536 x 1536 |
| `ultra` | 2048 x 2048 |

## Aspect Ratios

| Ratio | Width Multiplier | Height Multiplier |
|-------|-----------------|-------------------|
| `1:1` | 1.0 | 1.0 |
| `16:9` | 1.33 | 0.75 |
| `9:16` | 0.75 | 1.33 |
| `4:3` | 1.15 | 0.87 |
| `3:4` | 0.87 | 1.15 |
| `3:2` | 1.22 | 0.82 |
| `2:3` | 0.82 | 1.22 |

## Saved Designs

Generated images are tracked in a server-side manifest at `UPLOADS_DIR/ai-designs-manifest.json`:

```json
[
  {
    "url": "/uploads/ai-gen-1711691234567.png",
    "name": "AI Generated",
    "ts": 1711691234567
  }
]
```

The AI Image Creator canvas page provides a visual UI for browsing, naming, and managing saved designs.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/image-gen` | Generate image from prompt. Body: `{prompt, images?, model?, quality?, aspect?}` |
| `POST` | `/api/image-gen/enhance` | Enhance a rough idea into a detailed prompt |
| `GET` | `/api/image-gen/saved` | Load saved designs manifest |
| `POST` | `/api/image-gen/saved` | Add or replace entry in manifest |
| `DELETE` | `/api/image-gen/saved` | Remove entry by URL |

### Generate Request

```json
{
  "prompt": "a futuristic cityscape at sunset",
  "model": "nano-banana-pro-preview",
  "quality": "high",
  "aspect": "16:9",
  "images": []
}
```

The `images` array accepts base64-encoded images for image-to-image editing (Gemini only).

### Generate Response

```json
{
  "images": [
    {
      "mime_type": "image/png",
      "data": "base64...",
      "url": "/uploads/ai-gen-1711691234567.png"
    }
  ],
  "text": "Optional description from the model"
}
```

Images are saved to the server filesystem before the response is returned.
