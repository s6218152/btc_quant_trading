#!/bin/bash
# Run as root on PVE. Creates only a NEW VM; never overwrites an existing VM.
set -euo pipefail

VMID=${1:-102}
REVISION=${2:?Supply the full tested Git commit SHA}
[[ "$VMID" =~ ^[1-9][0-9]+$ && "$REVISION" =~ ^[0-9a-f]{40}$ ]] || exit 2
command -v qm >/dev/null
if qm status "$VMID" >/dev/null 2>&1; then
    echo "Refusing to overwrite existing VM $VMID" >&2
    exit 1
fi

WORK="/root/btc-paper-provision-$VMID"
mkdir -p "$WORK" /var/lib/vz/snippets
cp -n /etc/pve/storage.cfg "$WORK/storage.cfg.before" || true
# Preserve the existing storage content types and add cloud-init snippets.
python3 - <<'PY'
import json, subprocess
config = json.loads(subprocess.check_output(
    ['pvesh', 'get', '/storage/local', '--output-format', 'json']))
content = config['content'].split(',')
if 'snippets' not in content:
    subprocess.run(['pvesm', 'set', 'local', '--content', ','.join(content + ['snippets'])],
                   check=True)
PY

cd "$WORK"
wget -q https://cloud-images.ubuntu.com/noble/current/SHA256SUMS -O SHA256SUMS
wget -q https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img \
    -O noble-server-cloudimg-amd64.img
grep ' \*noble-server-cloudimg-amd64.img$' SHA256SUMS > image.sha256
sha256sum -c image.sha256

cat > "/var/lib/vz/snippets/btc-paper-$VMID.yaml" <<EOF
#cloud-config
hostname: btc-paper
manage_etc_hosts: true
ssh_pwauth: false
disable_root: true
package_update: true
packages:
  - qemu-guest-agent
  - docker.io
  - docker-compose-v2
  - git
runcmd:
  - [systemctl, enable, --now, qemu-guest-agent]
  - [systemctl, enable, --now, docker]
  - [bash, -lc, 'git clone https://github.com/s6218152/btc_quant_trading.git /opt/btc_quant_trading && cd /opt/btc_quant_trading && git checkout --detach $REVISION && mkdir -p user_data/dryrun && chown 1000:1000 user_data/dryrun && python3 scripts/research.py build > /var/log/btc-paper-build.log 2>&1 && python3 scripts/research.py dryrun-start']
final_message: 'Paper-trading VM cloud-init finished. Verify container health before acceptance.'
EOF

qm create "$VMID" --name btc-paper --memory 2048 --balloon 0 --cores 2 --cpu host \
    --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-pci --agent enabled=1 \
    --serial0 socket --vga serial0 --ostype l26
qm set "$VMID" --scsi0 "local-lvm:0,import-from=$WORK/noble-server-cloudimg-amd64.img,discard=on"
qm resize "$VMID" scsi0 20G
qm set "$VMID" --ide2 local-lvm:cloudinit --boot order=scsi0 --ipconfig0 ip=dhcp \
    --cicustom "user=local:snippets/btc-paper-$VMID.yaml" \
    --onboot 1 --startup order=3,up=30,down=120
qm set "$VMID" --description "BTC paper trading only. Revision $REVISION. No exchange keys. DHCP."
qm start "$VMID"
echo "VM $VMID started. Use qm guest exec $VMID after cloud-init installs the guest agent."
