module.exports = {
  version: "2.5",
  title: "OpenVoiceUI",
  description: "AI Voice Assistant — voice conversations, animated face, canvas, music generation, and more.",
  icon: "icon.png",
  menu: [
    {
      when: "!local.installed",
      html: "Install",
      href: "install.js",
    },
    {
      when: "local.installed && !local.running",
      html: "&#9654; Start",
      href: "start.js",
    },
    {
      when: "local.running",
      html: "Open",
      href: "http://localhost:{{local.PORT||5001}}",
    },
    {
      when: "local.running",
      html: "&#9724; Stop",
      href: "stop.js",
    },
    {
      when: "local.installed",
      html: "Update",
      href: "update.js",
    },
    {
      when: "local.installed",
      html: "Reinstall",
      href: "install.js",
    },
  ],
}
