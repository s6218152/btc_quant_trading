# Proxmox 模擬交易部署

專用虛擬機 `102 / btc-paper` 使用 Ubuntu 24.04 官方 cloud image、2 核心、
2GB RAM、20GB 磁碟，透過 DHCP 取得 IP。HA 與 Facebook 監控維持各自的 VM。
不在 PVE 主機直接安裝 Docker，不新增 SSH 金鑰或開放外網管理連接埠。

## 已完成驗收（2026-10-20）

- 程式版本：`d9710cd22167f5e8d4e291d503ece511695b4cb8`。
- Ubuntu 初始化完成，Docker 與 QEMU guest agent 正常運作。
- x86_64 部署環境 68 項測試與 Ruff 通過；策略 SHA256 與 Mac 相同。
- `dryrun-status` 顯示 `paper: running; health=healthy`、`Heartbeat: HEALTHY`。
- 已實際重開 VM 102，確認 Docker 與模擬服務自動恢復，重新檢查心跳正常。
- VM `onboot=1`、`startup=order=3,up=30,down=120`。
- Mac 副本已停止；原有資料保留。PVE 使用新的 10,000 USDT 模擬資金紀錄，
  沒有合併 Mac 資料庫（切換時 Mac 沒有模擬成交）。

PVE 驗收紀錄位於主機 `/root/btc-paper-acceptance.json`、
`/root/btc-paper-after-reboot.json`。本次只重開 VM 102，沒有重開整台 PVE 主機。
目前通過的是部署、單元測試與開機恢復驗收，不代表已完成長期穩定性或獲利驗證。

## 建立新 VM

先確認 VM ID 尚未使用、記憶體與儲存空間足夠，再於 PVE 主機 Shell 執行：

```sh
bash /root/provision-btc-paper.sh 102 <完整的已測試Git提交SHA>
```

腳本來源是本專案 `scripts/provision_pve_paper.sh`，下載到主機後先比對 SHA256。
腳本會拒絕覆寫既有 VM，校驗官方映像的 SHA256，並以 cloud-init 安裝 Ubuntu
套件庫的 Docker、Compose 與 QEMU guest agent，再 checkout 指定版本。
首次下載、安裝與建置需要時間；不要在尚未完成時重跑建立腳本。

主機端記錄：`/root/btc-paper-provision.log`；原始儲存設定與映像校驗記錄：
`/root/btc-paper-provision-102/`；cloud-init：`/var/lib/vz/snippets/btc-paper-102.yaml`。
腳本只為既有 `local` 儲存增加 `snippets` 內容類型，保留原有內容類型。

## 日常操作

在 Proxmox 網頁選擇 **pve 節點 → Shell**，不是 Mac 本機終端機，執行：

```sh
# 查看模擬交易健康狀態
qm guest exec 102 -- bash -lc 'cd /opt/btc_quant_trading && python3 scripts/research.py dryrun-status'

# 查看最近日誌
qm guest exec 102 -- bash -lc 'cd /opt/btc_quant_trading && python3 scripts/research.py dryrun-logs'

# 停止／啟動模擬交易，不刪除資料庫
qm guest exec 102 -- bash -lc 'cd /opt/btc_quant_trading && python3 scripts/research.py dryrun-stop'
qm guest exec 102 -- bash -lc 'cd /opt/btc_quant_trading && python3 scripts/research.py dryrun-start'
```

回傳 JSON 的 `exitcode: 0` 和 `Heartbeat: HEALTHY` 才代表該次健康檢查通過。
如果 guest agent 還沒啟動，不能使用以上指令；可先查看 VM Console 的開機訊息。
雲端映像未新增登入密碼；日常透過既有 PVE 管理權限與 guest agent 操作。

VM 內專案位於 `/opt/btc_quant_trading`，執行狀態位於 `user_data/dryrun/`，
建置日誌 `/var/log/btc-paper-build.log`，初始化日誌 `/var/log/cloud-init-output.log`。
Docker 設定為隨 VM 開機啟動，VM 設定為隨 PVE 開機啟動，順位 3。
Docker 程序退出後會依 `unless-stopped` 重啟；手動停止、主機斷電與 unhealthy
標記不是同一回事，詳細限制見 README。

更新時先停止服務並備份 VM／資料庫，再 checkout 指定提交、重新建置與啟動。
不要直接無檢查地 `git pull` 後長期執行。備份應包含整台 VM 與 runtime 目錄；
目前尚未替這台新增 VM 配置外接儲存定期備份與外部告警。

官方依據：[PVE cloud-init](https://github.com/proxmox/pve-docs/blob/master/qm-cloud-init.adoc)、
[Ubuntu 官方映像](https://cloud-images.ubuntu.com/noble/current/)。
