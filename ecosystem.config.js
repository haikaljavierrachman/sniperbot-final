module.exports = {
  apps: [{
    name: "hoodsniper-v16",
    script: "bot.py",
    args: "run",
    interpreter: "/usr/local/lib/hermes-agent/venv/bin/python3",
    cwd: __dirname,
    autorestart: true,
    watch: false,
    max_memory_restart: "450M",
    max_restarts: 10,
    restart_delay: 3000,
    error_file: "./logs/pm2-error.log",
    out_file: "./logs/pm2-out.log",
  }],
};
