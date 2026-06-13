"""策略注册中心(开闭原则基础设施)。

各扩展点(generation / parser / decomposition / consistency)各持一个 Registry 实例,
具体策略以 @registry.register("name") 自注册;应用层只按名称/能力取策略,从不 import 实现。
"""
from typing import Generic, TypeVar

from app.core.errors import NotFound

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str):
        self.kind = kind
        self._factories: dict[str, type[T]] = {}

    def register(self, name: str):
        def _wrap(cls: type[T]) -> type[T]:
            self._factories[name] = cls
            return cls

        return _wrap

    def create(self, name: str, **kwargs) -> T:
        cls = self._factories.get(name)
        if cls is None:
            raise NotFound(f"{self.kind} 策略未注册:{name}", {"available": list(self._factories)})
        return cls(**kwargs)

    def names(self) -> list[str]:
        return list(self._factories)
