# Сертификат геометрии RepairMach

- ID: `RMC-3993A5C272D43FBC`
- Методика: `RM91-GEOMETRY-CERT-1`
- Вердикт: **PASS_WITH_DECLARED_EXCLUSIONS**
- Создан: 2026-09-09T09:29:47+03:00
- MASTER: `8fcc33a6fb73db78f6e8111f7cda9637fd07aadd78be3516073af524643a4f01`
- MASTER неизменён: **да**
- Пригодность для решателя: **да**
- Представлена полная геометрия: **нет**
- Требуется гибридная замена: **да**

Сертификат подтверждает прослеживаемость и пригодность геометрии для указанной постановки. Он не подтверждает аэродинамическую точность и не заменяет сеточную или экспериментальную верификацию.

## Область действия

- Запрошенный Mach: `[[0.0, 0.8], [1.2, 2.2]]`
- Запрошенный alpha: `[0.0, 5.0]` град
- Квалификационный профиль: `full_anchor_envelope`
- Подтверждённых опорных точек: `15`

## Backends

| Backend | Пригоден | Постановка | Подтверждённая область |
|---|---:|---|---|
| vspaero | да | lifting | anchor_envelope |
| machline | нет | None | none |
| parasite_drag | да | full_geometry | geometry_only |
| hybrid | нет | declared_replacements_required | anchor_envelope |

## Преобразования

- `GEO-EXCL-001` — Wing2: clear_sets ({'1': False, '2': False} → {'1': False, '2': False})

## Исключения

- Wing2: Reference-only full-planform construction geometry; active aerodynamic wing is Wing
- Fuselage: mixed VSPAERO probe rejected; lifting-only twin accepted
- Gondola: mixed VSPAERO probe rejected; lifting-only twin accepted

## Диагностика

| Код | Уровень | Компонент | Сообщение |
|---|---|---|---|
| `GEO-EXCL-001` | WARNING | Wing2 | Компонент исключён явным правилом проекта; гибридная замена обязательна |
| `GEO-MACH-001` | BLOCKER |  | MachLine-критерий не выполнен; автоматическое абсолютное смещение вершин запрещено |
| `GEO-VSP-002` | WARNING |  | Смешанная постановка VSPAERO не прошла квалификацию; принят отдельный несущий двойник с явной гибридной заменой толстых тел |

## Артефакты

- Семантический аудит: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_092023_MiG_25RB_final_MiG-25RB_VSPAero_v1\semantic_audit.json`
- Исходная диагностика: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_092023_MiG_25RB_final_MiG-25RB_VSPAero_v1\native_diagnostics.json`
- План: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_092023_MiG_25RB_final_MiG-25RB_VSPAero_v1\transformation_plan.json`
- Отклонения: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_092023_MiG_25RB_final_MiG-25RB_VSPAero_v1\geometry_deltas.json`
