module.exports = {
  apps : [
    {
      name: 'secondbot-bot',
      script: 'run_bot_with_django.py',
      interpreter: 'python3',
      cwd: __dirname,
      autorestart: true,
      watch: false,
      max_restarts: 10,
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: '1',
      },
      error_file: 'logs/bot-error.log',
      out_file: 'logs/bot-out.log',
      merge_logs: true,
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
    }
  ]
};
