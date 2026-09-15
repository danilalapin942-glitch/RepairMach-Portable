# Сертификат геометрии RepairMach

- ID: `RMC-2B1288DC8BB5ACB1`
- Методика: `RM91-GEOMETRY-CERT-1`
- Вердикт: **PASS_WITH_DECLARED_EXCLUSIONS**
- Создан: 2026-09-09T09:20:23+03:00
- MASTER: `4caecd4a0015736a3506c88e57eead665a5a0051b7f1bcd01469cb43f16e974d`
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

Преобразования геометрии не потребовались (`PASS_NATIVE`).

## Исключения

- Fuselage: mixed VSPAERO probe rejected; lifting-only twin accepted
- Gondola: mixed VSPAERO probe rejected; lifting-only twin accepted

## Диагностика

| Код | Уровень | Компонент | Сообщение |
|---|---|---|---|
| `GEO-MACH-001` | BLOCKER |  | MachLine-критерий не выполнен; автоматическое абсолютное смещение вершин запрещено |
| `GEO-VSP-001` | WARNING |  | VSPAERO (mixed) сообщил диагностические особенности сетки; результаты допускаются только по конечности и сеточной сходимости |
| `GEO-VSP-001` | WARNING |  | VSPAERO (lifting) сообщил диагностические особенности сетки; результаты допускаются только по конечности и сеточной сходимости |
| `GEO-VSP-002` | WARNING |  | Смешанная постановка VSPAERO не прошла квалификацию; принят отдельный несущий двойник с явной гибридной заменой толстых тел |

## Артефакты

- Семантический аудит: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_090229_MiG_29_final_RepairMach91_M160_M170\semantic_audit.json`
- Исходная диагностика: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_090229_MiG_29_final_RepairMach91_M160_M170\native_diagnostics.json`
- План: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_090229_MiG_29_final_RepairMach91_M160_M170\transformation_plan.json`
- Отклонения: `C:\Users\admin\.codex\.chatgpt-projects\g-p-6910508be2a081919b9cb5881a96c92b\acceptance\geometry_certification_2026-09-09\20260909_090229_MiG_29_final_RepairMach91_M160_M170\geometry_deltas.json`
