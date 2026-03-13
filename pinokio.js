module.exports = {
  version: "3.7",
  title: "OpenVoiceUI",
  description: "AI Voice Assistant — voice conversations, animated face, canvas, music generation, and more.",
  icon: "icon.png",
  menu: async (kernel, info) => {
    let installed = info.exists("env")
    let running = {
      install: info.running("install.js"),
      start: info.running("start.js"),
      update: info.running("update.js"),
    }

    if (running.install) {
      return [{
        default: true,
        icon: "fa-solid fa-spinner fa-spin",
        text: "Installing...",
        href: "install.js",
      }]
    } else if (running.update) {
      return [{
        default: true,
        icon: "fa-solid fa-spinner fa-spin",
        text: "Updating...",
        href: "update.js",
      }]
    } else if (installed) {
      if (running.start) {
        let local = info.local("start.js")
        if (local && local.url) {
          return [{
            default: true,
            icon: "fa-solid fa-globe",
            text: "Open",
            href: local.url,
          }, {
            icon: "fa-solid fa-terminal",
            text: "Terminal",
            href: "start.js",
          }, {
            icon: "fa-solid fa-rotate",
            text: "Update",
            href: "update.js",
          }]
        } else {
          return [{
            default: true,
            icon: "fa-solid fa-terminal",
            text: "Terminal",
            href: "start.js",
          }]
        }
      } else {
        return [{
          default: true,
          icon: "fa-solid fa-play",
          text: "Start",
          href: "start.js",
        }, {
          icon: "fa-solid fa-rotate",
          text: "Update",
          href: "update.js",
        }, {
          icon: "fa-solid fa-plug",
          text: "Reinstall",
          href: "install.js",
        }]
      }
    } else {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "Install",
        href: "install.js",
      }]
    }
  }
}
