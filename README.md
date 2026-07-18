# Zedox Bot — Railway deployment

This package uses a Dockerfile, so Railway does not need `mise` to install Python.
All MongoDB database, collection, and document names remain unchanged, so existing bot data is preserved.

## Upload to GitHub
Upload these files to the repository root:
- bot.py
- Dockerfile
- railway.toml
- requirements.txt
- Procfile
- runtime.txt
- .dockerignore

Do not upload `.env.example` with real secrets.

## Railway variables
Create these variables separately:
- BOT_TOKEN
- ADMIN_ID
- MONGO_URI

Then redeploy. Railway should show that it is building from the Dockerfile.

## MongoDB Atlas
Network Access must permit Railway. For testing, `0.0.0.0/0` works, but use tighter rules when possible.

## Security
Rotate any bot token or database password that has been shared publicly.
