/**
 * JamBot Browser Companion — Command Parser
 *
 * Parses agent response text for command tags. Supports both new ref-based
 * commands (@e1) and legacy CSS selector commands for backward compatibility.
 *
 * Command format:
 *   [CLICK:@e4]              — click element by ref
 *   [CLICK:[aria-label="X"]] — click element by CSS selector (legacy)
 *   [FILL:@e1:some text]     — fill input with text
 *   [SELECT:@e3:Plumbing]    — select dropdown option
 *   [CHECK:@e5]              — check checkbox
 *   [UNCHECK:@e5]            — uncheck checkbox
 *   [HOVER:@e2]              — hover over element
 *   [PRESS:Enter]            — press keyboard key
 *   [SCROLL:+1200]           — scroll down 1200px
 *   [SCROLL:@e8]             — scroll element into view
 *   [SCROLL:top]             — scroll to top
 *   [NAVIGATE:https://...]   — navigate to URL
 *   [OPEN_TAB:https://...]   — open URL in new tab
 *   [READ_PAGE]              — extract full page text
 *   [WAIT:3]                 — wait N seconds
 *   [HIGHLIGHT:@e4]          — highlight element
 *   [START_TASK:description] — activate autonomous task loop
 *   [TASK_COMPLETE:summary]  — end task with summary
 *   [NOTE:text]              — scratchpad note (Phase 4)
 *
 * This module is used in sidepanel.js (not a content script).
 */

const JamBotCommandParser = (function () {
  'use strict';

  // Nested bracket pattern: matches content with CSS attribute selectors
  // e.g. [CLICK:[role="button"][aria-label*="Comment"]]
  const NB = '((?:[^\\[\\]]|\\[[^\\]]*\\])*)';

  // ── Parse Commands from Agent Response Text ────────────────────────────────

  function parseCommands(text) {
    const commands = [];

    const matchers = [
      // Ref-based and CSS selector commands
      { re: new RegExp(`\\[CLICK:${NB}\\]`, 'gi'),
        build: m => ({ type: 'click', ref: m[1].trim() }) },

      { re: new RegExp(`\\[FILL:([^:\\]]+):${NB}\\]`, 'gi'),
        build: m => ({ type: 'fill', ref: m[1].trim(), value: m[2].trim() }) },

      { re: new RegExp(`\\[SELECT:([^:\\]]+):${NB}\\]`, 'gi'),
        build: m => ({ type: 'select', ref: m[1].trim(), value: m[2].trim() }) },

      { re: new RegExp(`\\[CHECK:${NB}\\]`, 'gi'),
        build: m => ({ type: 'check', ref: m[1].trim() }) },

      { re: new RegExp(`\\[UNCHECK:${NB}\\]`, 'gi'),
        build: m => ({ type: 'uncheck', ref: m[1].trim() }) },

      { re: new RegExp(`\\[HOVER:${NB}\\]`, 'gi'),
        build: m => ({ type: 'hover', ref: m[1].trim() }) },

      { re: new RegExp(`\\[HIGHLIGHT:${NB}\\]`, 'gi'),
        build: m => ({ type: 'highlight', ref: m[1].trim() }) },

      { re: /\[PRESS:([^\]]+)\]/gi,
        build: m => ({ type: 'press', key: m[1].trim() }) },

      { re: /\[SCROLL:([^\]]+)\]/gi,
        build: m => ({ type: 'scroll', target: m[1].trim() }) },

      { re: /\[READ_PAGE\]/gi,
        build: () => ({ type: 'read_page' }) },

      { re: /\[NAVIGATE:([^\]]+)\]/gi,
        build: m => ({ type: 'navigate', url: m[1].trim() }) },

      { re: /\[OPEN_TAB:([^\]]+)\]/gi,
        build: m => ({ type: 'open_tab', url: m[1].trim() }) },

      { re: /\[WAIT:(\d+)\]/gi,
        build: m => ({ type: 'wait', ms: parseInt(m[1]) * 1000 }) },

      { re: /\[NOTE:([^\]]+)\]/gi,
        build: m => ({ type: 'note', text: m[1].trim() }) },
    ];

    for (const { re, build } of matchers) {
      let m;
      while ((m = re.exec(text)) !== null) {
        commands.push(build(m));
      }
    }

    return commands;
  }

  // ── Extract Task Control Tags ──────────────────────────────────────────────

  function extractStartTask(text) {
    const m = text.match(/\[START_TASK:([^\]]+)\]/i);
    return m ? m[1].trim() : null;
  }

  function extractTaskComplete(text) {
    const m = text.match(/\[TASK_COMPLETE:([^\]]*)\]/i);
    return m ? m[1].trim() : null;
  }

  // ── Strip All Tags from Display Text ───────────────────────────────────────

  function stripTags(text) {
    const nb = '(?:[^\\[\\]]|\\[[^\\]]*\\])*';
    return text
      .replace(new RegExp(`\\[HIGHLIGHT:${nb}\\]`, 'gi'), '')
      .replace(new RegExp(`\\[CLICK:${nb}\\]`, 'gi'), '')
      .replace(new RegExp(`\\[FILL:[^:\\]]+:${nb}\\]`, 'gi'), '')
      .replace(new RegExp(`\\[SELECT:[^:\\]]+:${nb}\\]`, 'gi'), '')
      .replace(new RegExp(`\\[CHECK:${nb}\\]`, 'gi'), '')
      .replace(new RegExp(`\\[UNCHECK:${nb}\\]`, 'gi'), '')
      .replace(new RegExp(`\\[HOVER:${nb}\\]`, 'gi'), '')
      .replace(/\[PRESS:[^\]]*\]/gi, '')
      .replace(/\[SCROLL:[^\]]*\]/gi, '')
      .replace(/\[READ_PAGE\]/gi, '')
      .replace(/\[NAVIGATE:[^\]]*\]/gi, '')
      .replace(/\[OPEN_TAB:[^\]]*\]/gi, '')
      .replace(/\[WAIT:\d+\]/gi, '')
      .replace(/\[NOTE:[^\]]*\]/gi, '')
      .replace(/\[START_TASK:[^\]]*\]/gi, '')
      .replace(/\[TASK_COMPLETE:[^\]]*\]/gi, '')
      .replace(/\[CANVAS(?:_URL|_MENU)?:[^\]]*\]/gi, '')
      .replace(/\[SLEEP\]/gi, '')
      .replace(/\[MUSIC_[^\]]*\]/gi, '')
      .replace(/```html[\s\S]*?```/gi, '')
      .replace(/```[\s\S]*?```/g, '');
  }

  // ── Classify command for routing ───────────────────────────────────────────
  // Commands that need the content script vs commands handled by background.js

  function classifyCommand(cmd) {
    switch (cmd.type) {
      case 'navigate':
      case 'open_tab':
      case 'wait':
        return 'background'; // handled by background.js (tab management)
      case 'note':
        return 'local'; // handled by sidepanel locally (scratchpad)
      default:
        return 'content'; // executed in content script via action executor
    }
  }

  return {
    parseCommands,
    extractStartTask,
    extractTaskComplete,
    stripTags,
    classifyCommand,
  };
})();
