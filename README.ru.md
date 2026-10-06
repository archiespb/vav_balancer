# VAV Ventilation Balancer для Home Assistant

[![GitHub release](https://img.shields.io/github/v/release/archiespb/vav_balancer)](https://github.com/archiespb/vav_balancer/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Tests](https://img.shields.io/github/actions/workflow/status/archiespb/vav_balancer/tests.yml?label=tests)](https://github.com/archiespb/vav_balancer/actions/workflows/tests.yml)
[![Validate](https://img.shields.io/github/actions/workflow/status/archiespb/vav_balancer/validate.yml?label=validate)](https://github.com/archiespb/vav_balancer/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/archiespb/vav_balancer)](LICENSE)
![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-blue)

![GitHub commits since latest release](https://img.shields.io/github/commits-since/archiespb/vav_balancer/latest?style=plastic)
![GitHub commit activity](https://img.shields.io/github/commit-activity/m/archiespb/vav_balancer?style=plastic)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=archiespb&repository=vav_balancer&category=integration)

[English version](README.md)

<img src="https://raw.githubusercontent.com/archiespb/vav_balancer/main/custom_components/vav_balancer/brand/icon@2x.png" width="128" alt="VAV Balancer">

Универсальный асинхронный балансировщик приточно-вытяжной вентиляции (VAV — Variable Air Volume, «переменный объём воздуха») с многошаговой настройкой через UI. Интеграция не привязана к конкретным комнатам, моделям вентиляторов или типам датчиков: приток и вытяжка собираются из любых уже существующих в вашем Home Assistant сущностей `fan`, а их поведение задаётся целиком в мастере настройки — без единой строчки YAML.

Возможности коротко: любое число приточных и вытяжных вентиляторов (ступенчатых или процентных), правила по датчикам с динамическими порогами (в том числе со значением другого датчика), антидребезг и гистерезис против «дёргания» у порога, дневной/ночной потолок, режим «только чтение» для автономных вентиляторов, постоянное поддержание баланса приток↔вытяжка с защитой от перекосов, экспорт/импорт конфигурации, готовые сенсоры для графиков на dashboard и диагностика одной кнопкой.

Полное описание каждого механизма — в **[DOCS.ru.md](DOCS.ru.md)**.

Требуется Home Assistant **2024.12** или новее.

## Установка

### Через HACS

Интеграция пока не входит в стандартный каталог HACS, поэтому добавьте её как пользовательский репозиторий:

1. **HACS → Интеграции → ⋮ (в правом верхнем углу) → Пользовательские репозитории**.
2. Введите URL репозитория (`https://github.com/archiespb/vav_balancer`), выберите категорию **Integration** и нажмите **Добавить**.
3. Найдите **VAV Ventilation Balancer** в HACS и нажмите **Скачать**.
4. Перезапустите Home Assistant.
5. **Настройки → Устройства и службы → Добавить интеграцию → VAV Ventilation Balancer**.

### Вручную

1. Скопируйте папку `custom_components/vav_balancer` в каталог `config/custom_components/` вашего Home Assistant.
2. Перезапустите Home Assistant.
3. **Настройки → Устройства и службы → Добавить интеграцию → VAV Ventilation Balancer**.

## Удаление

1. **Настройки → Устройства и службы → VAV Ventilation Balancer → ⋮ → Удалить**. Это остановит оба контура, снимет все трекеры состояний и удалит запись интеграции вместе со всеми её сущностями и единственным устройством; сами вентиляторы и датчики, которые вы в ней использовали, не затрагиваются.
2. Чтобы убрать и сам код интеграции, после удаления записи остановите Home Assistant, удалите папку `config/custom_components/vav_balancer` и перезапустите снова.

Если конфигурация нужна для переноса на другой экземпляр, перед удалением экспортируйте её (**Настроить → Экспортировать текущую конфигурацию** — подробнее в [DOCS.ru.md](DOCS.ru.md#экспорт-и-импорт-конфигурации)).

## Структура репозитория

| Путь | Назначение |
|---|---|
| `custom_components/vav_balancer/manifest.json` | Манифест интеграции (домен, версия, зависимости) |
| `custom_components/vav_balancer/const.py` | Ключи конфигурации, значения по умолчанию, лимиты |
| `custom_components/vav_balancer/models.py` | Модели вентилятора (`FanModel`) и правила датчика (`Rule`) |
| `custom_components/vav_balancer/balancer.py` | Чистая математика баланса (без обращений к HA) |
| `custom_components/vav_balancer/controller.py` | Главный контроллер: контур 1 (расчёт) и контур 2 (исполнение) |
| `custom_components/vav_balancer/entity.py` | Общая базовая сущность для всех платформ |
| `custom_components/vav_balancer/fan.py` | Единственная мастер-сущность `FanEntity` |
| `custom_components/vav_balancer/sensor.py` | Сенсоры производительности для графиков на dashboard |
| `custom_components/vav_balancer/binary_sensor.py` | Сенсоры «ночной режим» / «режим дома» |
| `custom_components/vav_balancer/diagnostics.py` | Выгрузка диагностики |
| `custom_components/vav_balancer/config_flow.py` | Мастер настройки, `OptionsFlowHandler`, экспорт/импорт |
| `custom_components/vav_balancer/translations/` | Тексты интерфейса (en, ru) |
| `tests/` | Набор `pytest` (см. [CONTRIBUTING.md](CONTRIBUTING.md)) |
| `dashboard_example.yaml` | Пример Lovelace-дашборда с графиками и правилами |

## Быстрый старт настройки

И при первом добавлении, и в **Настроить** (`OptionsFlow`) сначала открывается меню: пошаговый мастер, импорт или (только для уже настроенной интеграции) экспорт конфигурации.

Пошаговый мастер: выбор приточных и вытяжных вентиляторов → для каждого вентилятора тип управления (ступени/проценты), карта расходов, дневной/ночной потолок и правила по датчикам → глобальные параметры (минимумы присутствия, тихие часы, интервал команд, допуск по давлению). Полное описание каждого шага, всех полей правил (динамический порог, антидребезг, гистерезис) и формата экспорта/импорта — в [DOCS.ru.md](DOCS.ru.md#мастер-настройки--полное-описание-шагов).

## Мастер-сущность и dashboard

Интеграция создаёт одну мастер-сущность `fan.vav_balancer` (включение/выключение фактического управления железом) и набор сенсоров (`sensor.vav_balancer_*`, `binary_sensor.vav_balancer_*`) для графиков цели/факта по притоку, вытяжке и давлению — без сторонних карточек. Полный список сущностей и их атрибутов, а также пример готового dashboard — в [DOCS.ru.md](DOCS.ru.md#сущности-для-dashboard).

## Логирование и диагностика

```yaml
logger:
  default: warning
  logs:
    custom_components.vav_balancer: debug
```

Для отчёта об ошибке: **Настройки → Устройства и службы → VAV Ventilation Balancer → Скачать диагностику**. Подробнее — в [DOCS.ru.md](DOCS.ru.md#логирование-и-диагностика).

## Ограничения

- Одна запись интеграции на установку.
- Значения `airflow_map` должны не убывать; карта задаётся вручную по паспорту вентилятора или замерам.
- Минимумы по присутствию применяются как нижняя граница к приточным и вытяжным вентиляторам одновременно. Если они превышают возможности другой стороны, приоритет у положительного давления.

## Разработка и тесты

См. [CONTRIBUTING.md](CONTRIBUTING.md).

## Об использовании ИИ

Значительная часть кода написана с помощью ИИ-ассистента (Claude, Anthropic) под руководством автора. Архитектура, требования и тестирование на реальном оборудовании — за автором. Код проверялся вручную, но, как и любой другой, может содержать ошибки — сообщайте о них в Issues.