# Installation Guide

This app shells out to `keytool` (part of the JDK) and `zipalign` /
`apksigner` (part of the Android SDK build-tools). Follow the steps
below for your platform, or just use the provided `Dockerfile`, which
does all of this automatically.

---

## 1. Install Python 3.13

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.13 python3.13-venv python3-pip
```

## 2. Install a JDK (provides `keytool`)

```bash
sudo apt install -y openjdk-17-jdk
java -version
keytool -help   # should print usage
```

## 3. Install Android SDK command-line tools (provides `zipalign`, `apksigner`)

```bash
export ANDROID_SDK_ROOT=$HOME/android-sdk
mkdir -p $ANDROID_SDK_ROOT/cmdline-tools
cd /tmp
curl -O https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip commandlinetools-linux-11076708_latest.zip -d $ANDROID_SDK_ROOT/cmdline-tools
mv $ANDROID_SDK_ROOT/cmdline-tools/cmdline-tools $ANDROID_SDK_ROOT/cmdline-tools/latest

yes | $ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager --licenses
$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager "build-tools;34.0.0" "platform-tools"
```

Add the build-tools directory to your `PATH` (put this in `~/.bashrc`
or your systemd service's `Environment=` directive):

```bash
export ANDROID_SDK_ROOT=$HOME/android-sdk
export PATH="$ANDROID_SDK_ROOT/build-tools/34.0.0:$ANDROID_SDK_ROOT/platform-tools:$PATH"
```

Verify:

```bash
zipalign --help
apksigner help
```

> Note: the exact download URL/version for `commandlinetools` changes
> periodically — check
> https://developer.android.com/studio#command-line-tools-only for the
> current link if the one above 404s.

## 4. Clone / place the app and install Python deps

```bash
cd apk_signer
python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

If `keytool`, `zipalign`, or `apksigner` are not on your `PATH`, set
`KEYTOOL_BIN`, `ZIPALIGN_BIN`, `APKSIGNER_BIN` in `.env` to their
absolute paths instead.

## 5. Run

```bash
# Dev server
python app.py

# Production
gunicorn -k gthread --threads 8 -w 2 -b 0.0.0.0:8000 --timeout 600 app:app
```

Open **http://localhost:8000**.

## 6. (Optional) systemd service

```ini
# /etc/systemd/system/apk-signer.service
[Unit]
Description=APK Signer Web App
After=network.target

[Service]
User=appuser
WorkingDirectory=/opt/apk_signer
Environment="ANDROID_SDK_ROOT=/opt/android-sdk"
Environment="PATH=/opt/android-sdk/build-tools/34.0.0:/opt/android-sdk/platform-tools:/usr/bin"
ExecStart=/opt/apk_signer/venv/bin/gunicorn -k gthread --threads 8 -w 2 -b 0.0.0.0:8000 --timeout 600 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now apk-signer
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Required tool 'zipalign' was not found on PATH` | Add build-tools dir to PATH or set `ZIPALIGN_BIN` |
| `apksigner: command not found` | Same as above, set `APKSIGNER_BIN` |
| Log stream never updates in browser | Ensure your reverse proxy doesn't buffer SSE (`proxy_buffering off;` in Nginx) and Gunicorn uses `-k gthread` |
| `413 Request Entity Too Large` | Raise `MAX_UPLOAD_MB` in `.env` (and any reverse-proxy body-size limit) |
