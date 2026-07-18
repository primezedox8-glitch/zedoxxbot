# Zedox Bot — Railway Deployment

## Required Railway variables

- `BOT_TOKEN`
- `ADMIN_ID`
- `MONGO_URI`
- `MISE_PYTHON_GITHUB_ATTESTATIONS=false` (only if Railway shows the mise attestation error)

## Files

Keep these exact names in the GitHub repository root:

- `bot.py`
- `Procfile`
- `requirements.txt`
- `runtime.txt`

Do not rename `Procfile` to `Procfile.txt`.

## Railway

Connect the GitHub repository, add the variables, and redeploy. The worker start command is:

`python bot.py`
