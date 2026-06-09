"""Regresión: el módulo `api` debe cargarse el ÚLTIMO en ENABLED_MODULES.

`api.setup` construye la app FastAPI capturando en `app.state` (vía getattr) las
dependencias ya presentes en el bot (`signup_manager`, `registry_store`,
`event_bus`, `bridge_service`, `guild_config_store`). Si `api` se cargara antes
que un módulo proveedor (p. ej. `bridge`), esa dependencia quedaría como `None`
y su endpoint asociado fallaría en producción (las pruebas con un bot doble no
lo detectan).

Este test no importa `modules` (eso arrastraría `discord`): parsea el orden
declarado en `modules/__init__.py` de forma textual.
"""
import ast
import os


MODULES_INIT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "modules", "__init__.py",
)


def _enabled_modules_order():
    """Devuelve la lista de nombres de módulo en ENABLED_MODULES, en orden."""
    with open(MODULES_INIT, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ENABLED_MODULES":
                    elements = node.value.elts  # type: ignore[attr-defined]
                    return [el.id for el in elements]
    raise AssertionError("No se encontró ENABLED_MODULES en modules/__init__.py")


def test_api_is_loaded_last():
    order = _enabled_modules_order()
    assert order, "ENABLED_MODULES está vacío"
    assert order[-1] == "api", (
        f"El módulo 'api' debe ser el último en ENABLED_MODULES; orden actual: {order}"
    )


def test_providers_loaded_before_api():
    order = _enabled_modules_order()
    api_index = order.index("api")
    for provider in ("signups", "registry", "bridge"):
        assert provider in order, f"Falta el módulo proveedor '{provider}'"
        assert order.index(provider) < api_index, (
            f"'{provider}' debe cargarse antes que 'api'; orden actual: {order}"
        )
