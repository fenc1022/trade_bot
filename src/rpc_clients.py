from __future__ import annotations

from web3 import HTTPProvider, Web3


def build_web3_client(rpc_url: str, request_timeout: int = 10) -> Web3:
    provider = HTTPProvider(
        endpoint_uri=rpc_url,
        request_kwargs={"timeout": request_timeout},
    )
    return Web3(provider)
