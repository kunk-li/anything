# -*- coding: utf-8 -*-
"""
SecurityMixin (从 impl.py 拆出 — 鉴权/租户解析, 零行为变更)

    _reconcile_tenant_id / _build_key_to_tenant_index / _resolve_tenant_from_auth /
    _extract_bearer_token / _decode_jwt / _is_internal_ip / _is_internal_host /
    _check_auth / _check_admin

依赖 ApiService (self): __init__ 建的 auth 配置 (auth_enabled/auth_type/jwt_secret/
    admin_api_keys/_key_to_tenant 等)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import time
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from deps_module import StartupError


class SecurityMixin:
    """API-Key/JWT 鉴权 + 内网判定 + 租户解析 + admin 闸。"""

    def _reconcile_tenant_id(self, request: Request, body: Dict[str, Any], trace_id: str) -> None:
        """处理 auth_tenant_id 与 body['tenant_id'] 的冲突。

        规则(见 docs/multi-tenancy-design.md §4.3):
            - 仅 auth_tenant_id 存在 -> 用 auth
            - 仅 body 存在(认证未携带): 仅 internal IP 允许;否则保留 body(后续 RequestHandler 补 default)
            - 两者一致 -> 用 auth (等价)
            - 两者不一致 -> 用 auth + 记 ERROR (疑似越权)

        本方法直接修改 body 字典, body['tenant_id'] 最终为认证产物或保留原值。
        """
        auth_tid = self._resolve_tenant_from_auth(request)
        body_tid = body.get("tenant_id")

        if auth_tid:
            if body_tid and body_tid != auth_tid:
                self.logger.error(
                    f"[security] tenant_id mismatch: auth={auth_tid!r} body={body_tid!r} "
                    f"trace_id={trace_id} -- 疑似越权尝试, 强制使用认证产物"
                )
                # metrics counter (本期借用 errors_by_code, 后续可单独记 anything_tenant_mismatch_total)
            body["tenant_id"] = auth_tid
            return

        # 没有认证产物: 若 body 显式声明 tenant_id, 仅 internal IP 允许保留
        if body_tid and not self._is_internal_ip(request):
            self.logger.warning(
                f"[security] body 声明 tenant_id={body_tid!r} 但请求未认证且非 internal IP, "
                f"忽略 body 声明 (后续 RequestHandler 会补 default), trace_id={trace_id}"
            )
            body.pop("tenant_id", None)
        # else: 内部 IP 保留 body['tenant_id'], 或 body 本来就没声明 -> 不动

    def _build_key_to_tenant_index(self, raw: Any) -> Dict[str, str]:
        """从 yaml security.api_keys 构造 key -> tenant_id 反向索引。

        支持两种输入格式:
            - list ["k1", "k2"] (老格式, 全部映射到 default + 启动 WARN)
            - dict {"tenant-a": ["k1"], "default": ["k2"]} (新格式)
              一个 key 严格只能绑一个 tenant_id (决议 3); 多绑触发 StartupError

        见 docs/multi-tenancy-design.md §5.3
        """
        if isinstance(raw, list):
            if raw:
                self.logger.warning(
                    f"[security] detected legacy api_keys list format; "
                    f"all {len(raw)} keys mapped to tenant='default'. "
                    f"Multi-tenancy disabled. Migrate to tenant->keys dict to enable."
                )
            return {str(k): "default" for k in raw if k}

        if isinstance(raw, dict):
            seen: Dict[str, str] = {}
            for tid, keys in raw.items():
                if not isinstance(keys, list):
                    raise StartupError(
                        component="security.api_keys",
                        reason=f"tenant '{tid}' 的 keys 应是 list, 实际是 {type(keys).__name__}",
                        hint="格式: tenant_id: [\"key1\", \"key2\"]",
                    )
                for key in keys:
                    key_str = str(key) if key is not None else ""
                    if not key_str:
                        continue
                    if key_str in seen and seen[key_str] != tid:
                        raise StartupError(
                            component="security.api_keys",
                            reason=(
                                f"API key bound to multiple tenants: "
                                f"'{tid}' vs '{seen[key_str]}'"
                            ),
                            hint="一个 API key 只能绑一个 tenant_id;跨租户访问请发多个 key",
                        )
                    seen[key_str] = tid
            return seen

        # 配置类型异常 -> 空映射 (等价于无鉴权配置)
        if raw not in (None, [], {}):
            self.logger.warning(
                f"[security] api_keys 配置类型不支持: {type(raw).__name__}; 视为空映射"
            )
        return {}

    def _resolve_tenant_from_auth(self, request: Request) -> Optional[str]:
        """从认证产物提取 tenant_id。

        - apikey: 查 _key_to_tenant 反向映射
        - jwt: HS256 验签后取 payload.tenant_id claim
        - none: 返回 None
        """
        if not self.auth_enabled or self.auth_type == "none":
            return None
        if self.auth_type == "apikey":
            api_key = request.headers.get("X-API-Key")
            if api_key:
                return self._key_to_tenant.get(api_key)
        if self.auth_type == "jwt":
            payload = self._decode_jwt(self._extract_bearer_token(request))
            if payload:
                tid = payload.get("tenant_id")
                return str(tid) if tid else None
        return None

    @staticmethod
    def _extract_bearer_token(request: Request) -> str:
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _decode_jwt(self, token: str) -> Optional[Dict[str, Any]]:
        """HS256 JWT 验签 + exp 检查 (纯标准库, 不引第三方依赖)。

        成功返回 payload dict; 格式/算法/签名/过期任一不过 -> None。
        只接受 alg=HS256 — 防 alg=none / 算法混淆降级攻击。
        """
        if not token or not self.jwt_secret:
            return None
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")

            def _b64d(s: str) -> bytes:
                return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

            header = json.loads(_b64d(header_b64))
            if not isinstance(header, dict) or header.get("alg") != "HS256":
                return None
            expected = hmac.new(
                self.jwt_secret.encode("utf-8"),
                f"{header_b64}.{payload_b64}".encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected, _b64d(sig_b64)):
                return None
            payload = json.loads(_b64d(payload_b64))
            if not isinstance(payload, dict):
                return None
            exp = payload.get("exp")
            if exp is not None and time.time() >= float(exp):
                return None
            return payload
        except Exception:
            return None  # 畸形 token 一律视为未认证, 不抛协议层 500

    def _is_internal_ip(self, request: Request) -> bool:
        """判断请求源 IP 是否在 internal_whitelist 中 (§4.3 冲突处理用)。"""
        client = request.client.host if request.client else ""
        return self._is_internal_host(client)

    def _is_internal_host(self, client: str) -> bool:
        """internal_whitelist 匹配核心 (HTTP / WS 共用)。

        entry 支持三种形态:
          - IP / CIDR ("127.0.0.1", "10.0.0.0/8") -> ipaddress 网段判定
          - 主机名 ("testclient") -> 精确匹配
          - 旧式前缀 ("10.0.0.") -> 按 "." 边界前缀匹配
            (修复: 原 startswith 裸前缀会让 "10.0" 误命中 "10.01.2.3")
        """
        if not self.internal_whitelist or not client:
            return False
        try:
            client_ip = ipaddress.ip_address(client)
        except ValueError:
            client_ip = None  # TestClient 等场景 host 是主机名字符串
        for entry in self.internal_whitelist:
            entry_s = str(entry).strip()
            if not entry_s:
                continue
            if client_ip is not None:
                try:
                    net = ipaddress.ip_network(entry_s, strict=False)
                except ValueError:
                    net = None
                if net is not None:
                    if client_ip.version == net.version and client_ip in net:
                        return True
                    continue  # entry 是合法网段但不含该 IP, 不再退回字符串匹配
            if client == entry_s:
                return True
            if client.startswith(entry_s if entry_s.endswith(".") else entry_s + "."):
                return True
        return False

    def _check_auth(self, request: Request, trace_id: str) -> Optional[JSONResponse]:
        """鉴权检查:仅处理协议层鉴权,不涉及业务逻辑。

        成功 -> 返回 None (调用方继续, tenant_id 通过 _resolve_tenant_from_auth 读取)
        失败 -> 返回 JSONResponse (401)
        """
        if not self.auth_enabled or self.auth_type == "none":
            return None

        if self.auth_type == "apikey":
            api_key = request.headers.get("X-API-Key")
            if not api_key or api_key not in self._key_to_tenant:
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "AUTH_REQUIRED",
                        "message": "未认证或 API Key 无效",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )

        if self.auth_type == "jwt":
            # secret 未配置时 _decode_jwt 恒 None -> 全部 401 (fail-closed)
            if self._decode_jwt(self._extract_bearer_token(request)) is None:
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "AUTH_REQUIRED",
                        "message": "未认证或 JWT 无效/已过期 (Authorization: Bearer <token>)",
                        "data": None,
                        "trace_id": trace_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": trace_id},
                )

        return None

    def _check_admin(self, request: Request, trace_id: str) -> Optional[JSONResponse]:
        """管理写端点二级校验 (/config/models 等)。

        配置 security.admin_api_keys 后仅持 admin key 可调, 否则 403;
        未配置时不额外设防, 维持旧约定 (生产在网关层屏蔽 /config/*)。
        """
        if not self.admin_api_keys:
            return None
        api_key = request.headers.get("X-API-Key") or ""
        if api_key in self.admin_api_keys:
            return None
        return JSONResponse(
            status_code=403,
            content={
                "code": "AUTH_FORBIDDEN",
                "message": "该操作需要 admin API key (security.admin_api_keys)",
                "data": None,
                "trace_id": trace_id,
                "retryable": False,
                "details": None,
            },
            headers={"X-Request-Id": trace_id},
        )

    # ------------------------- P1: uploads 保留期清理 -------------------------

