import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/installation',
        'getting-started/configuration',
        'getting-started/openclaw-requirements',
      ],
    },
    {
      type: 'category',
      label: 'Features',
      collapsed: false,
      items: [
        'features/canvas-system',
        'features/music-system',
        'features/desktop-interface',
        'features/image-generation',
        'features/voice-stt-flow',
        'features/faces',
        'features/admin-dashboard',
      ],
    },
    {
      type: 'category',
      label: 'Customization',
      items: [
        'customization/profiles',
        'customization/tts-providers',
        'customization/themes',
      ],
    },
    {
      type: 'category',
      label: 'Extending OpenVoiceUI',
      items: [
        'extending/plugin-system',
        'extending/building-canvas-pages',
        'extending/building-face-plugins',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: [
        'reference/api',
        'reference/architecture',
        'reference/agent-tags',
      ],
    },
    {
      type: 'category',
      label: 'Contributing',
      items: [
        'contributing/development-setup',
        'contributing/pr-checklist',
        'contributing/security',
      ],
    },
  ],
};

export default sidebars;
