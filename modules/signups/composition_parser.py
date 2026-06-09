"""Parser de composición ad-hoc para eventos de inscripción (Req 2.1).

Convierte una cadena tipo ``"backend:2, frontend:3, qa:1"`` en una lista de
pares ``(clave, {"emoji": None, "plazas": int})`` apta para
:meth:`EventManager.validate_composition`. La forma de **lista de pares**
preserva las claves duplicadas, de modo que el manager pueda detectarlas
(Req 2.5); un dict las colapsaría silenciosamente.

Este módulo es puro (no importa ``discord``) para poder testearse de forma
aislada, igual que `manager` y `store`.
"""

try:  # Uso normal como parte del paquete ``modules.signups``.
    from .manager import CompositionError
except ImportError:  # pragma: no cover - carga aislada en tests (domain_loader).
    # Cuando se carga este archivo directamente por ruta (sin contexto de
    # paquete) el import relativo falla; cargamos ``manager.py`` por ruta para
    # reutilizar exactamente la misma clase de error. ``manager`` no depende de
    # ``discord``, así que esto es seguro sin el paquete instalado.
    import importlib.util as _ilu
    import os as _os

    _MANAGER_PATH = _os.path.join(_os.path.dirname(__file__), "manager.py")
    _spec = _ilu.spec_from_file_location("signups_manager_for_parser", _MANAGER_PATH)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    CompositionError = _mod.CompositionError


def parse_composition(text):
    """Parsea ``text`` en una lista de pares ``(clave, cfg)``.

    Formato esperado: segmentos ``rol:plazas`` separados por comas, con espacios
    opcionales. Las claves se normalizan a minúsculas. Las plazas se interpretan
    como enteros ≥ 1.

    Lanza :class:`CompositionError` (la misma clase que usa el manager, para que
    el cog tenga un manejo uniforme) con estos códigos:
      - ``"empty"``          → cadena vacía o solo espacios/comas.
      - ``"malformed"``      → segmento sin ``:`` o sin nombre de rol.
      - ``"invalid_plazas"`` → plazas no entero o < 1.

    Las claves duplicadas NO se detectan aquí (se preservan en la lista de
    pares); de eso se encarga :meth:`EventManager.validate_composition`.
    """
    if text is None or not str(text).strip():
        raise CompositionError(
            "empty", "La composición no puede estar vacía. Usa 'rol:plazas, ...'.")

    pairs = []
    for raw_segment in str(text).split(","):
        segment = raw_segment.strip()
        if not segment:
            # Coma sobrante o doble coma: se ignora.
            continue

        if ":" not in segment:
            raise CompositionError(
                "malformed",
                f"Segmento inválido: '{segment}'. Usa el formato 'rol:plazas'.",
                role=segment)

        key_part, _, plazas_part = segment.partition(":")
        key = key_part.strip().lower()
        plazas_str = plazas_part.strip()

        if not key:
            raise CompositionError(
                "malformed",
                f"Falta el nombre del rol en '{segment}'.",
                role=segment)

        try:
            plazas = int(plazas_str)
        except ValueError:
            raise CompositionError(
                "invalid_plazas",
                f"El rol '{key}' debe tener un número entero de plazas.",
                role=key)

        if plazas < 1:
            raise CompositionError(
                "invalid_plazas",
                f"El rol '{key}' debe tener al menos 1 plaza.",
                role=key)

        pairs.append((key, {"emoji": None, "plazas": plazas}))

    if not pairs:
        raise CompositionError(
            "empty", "La composición no puede estar vacía. Usa 'rol:plazas, ...'.")

    return pairs
