#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# Stop this disposable test instance eight hours after every boot.
cat >/etc/systemd/system/qwen-autostop.service <<'EOF'
[Unit]
Description=Stop the Qwen EC2 test instance
[Service]
Type=oneshot
ExecStart=/usr/sbin/shutdown -h now
EOF
cat >/etc/systemd/system/qwen-autostop.timer <<'EOF'
[Unit]
Description=Eight-hour Qwen test limit
[Timer]
OnBootSec=8h
Unit=qwen-autostop.service
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now qwen-autostop.timer

# The AWS Deep Learning Base AMI supplies the NVIDIA GPU driver.
apt-get update
apt-get install -y ca-certificates curl gnupg openssl
if ! command -v docker >/dev/null; then
  apt-get install -y docker.io
fi
if ! command -v nvidia-ctk >/dev/null; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey |
    gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list |
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      >/etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update
  apt-get install -y nvidia-container-toolkit
fi
nvidia-ctk runtime configure --runtime=docker
systemctl enable docker
systemctl restart docker
nvidia-smi

install -d -m 700 /opt/qwen
umask 077
if [[ ! -s /opt/qwen/server.env ]]; then
  printf 'VLLM_API_KEY=%s\n' "$(openssl rand -hex 32)" >/opt/qwen/server.env
fi
cat >/opt/qwen/run.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
docker rm -f qwen-vllm >/dev/null 2>&1 || true
exec docker run --rm --name qwen-vllm --gpus all --network host --shm-size 2g \
  --env-file /opt/qwen/server.env \
  -v /opt/qwen/huggingface:/root/.cache/huggingface \
  vllm/vllm-openai:v0.18.1@sha256:228113d30448941e7a845f57ef0b3d3ea74ffda81be72ded4f8d6dfab0124fe6 \
  RedHatAI/Qwen3.5-9B-quantized.w4a16 \
  --revision a398088c4228b0ae0c8c78df88fd1e4bf445f068 \
  --tokenizer Qwen/Qwen3.5-9B \
  --tokenizer-revision c202236235762e1c871ad0ccb60c8ee5ba337b9a \
  --served-model-name qwen3.5-9b \
  --host 127.0.0.1 --port 8000 \
  --language-model-only --max-model-len 262144 --max-num-seqs 2 --enforce-eager \
  --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --default-chat-template-kwargs '{"enable_thinking": false}'
EOF
chmod 700 /opt/qwen/run.sh
cat >/etc/systemd/system/qwen-vllm.service <<'EOF'
[Unit]
Description=Qwen3.5-9B W4A16 vLLM test service
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target
[Service]
ExecStart=/opt/qwen/run.sh
ExecStop=/usr/bin/docker stop qwen-vllm
Restart=on-failure
RestartSec=15
TimeoutStopSec=60
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now qwen-vllm
