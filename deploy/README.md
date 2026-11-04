# 部署（腾讯云轻量 2C2G · Ubuntu 22.04）

线上地址：https://wjnb.oneoneoneimno1.site （nginx Basic Auth 保护）

| 项目 | 位置 |
| --- | --- |
| 代码 | `/opt/evidenceflow`（通过 rsync 推送，服务器连不上 GitHub） |
| venv | `/opt/evidenceflow/.venv`（Python 3.12 来自 deadsnakes PPA；依赖走清华 PyPI 镜像） |
| 密钥 | `/opt/evidenceflow/.env`（权限 600，不在仓库里） |
| 服务 | `systemctl status evidenceflow`，日志 `journalctl -u evidenceflow -f` |
| 反代 | `/etc/nginx/sites-available/evidenceflow`，证书由 certbot 自动续期 |
| 密码 | `sudo htpasswd -B /etc/nginx/.htpasswd 用户名` 增改用户 |
| swap | 2 GB `/swapfile`（模型加载后进程约 850 MB） |

## 更新代码

```bash
./deploy/deploy.sh          # 推送代码并重启
./deploy/deploy.sh --deps   # pyproject.toml 变了时
```

## 从零搭建（摘要）

1. `fallocate` 2 GB swap；`apt install nginx certbot python3-certbot-nginx apache2-utils`；deadsnakes PPA 装 `python3.12 python3.12-venv`；安装 uv。
2. rsync 代码到 `/opt/evidenceflow`，`uv venv --python /usr/bin/python3.12 .venv`，`UV_INDEX_URL=<镜像> uv pip install -r pyproject.toml`。
   PyPI 的 Linux torch 自带 CUDA 库（约 5 GB，可用但浪费）；想省磁盘可从 `https://mirrors.aliyun.com/pytorch-wheels/cpu/` 装 `+cpu` 版。
3. `cp .env.template .env`，填 `LLM_PROVIDER`、`DEEPSEEK_API_KEY`、`SERPER_API_KEY`。
4. 复制 `deploy/evidenceflow.service` 到 `/etc/systemd/system/`，`systemctl enable --now evidenceflow`。
5. 复制 `deploy/nginx-evidenceflow.conf` 到 `sites-available` 并启用；`htpasswd -cB` 建用户；`certbot --nginx -d 域名 --redirect`。
6. 腾讯云防火墙放行 22/80/443；域名 A 记录指向公网 IP。
