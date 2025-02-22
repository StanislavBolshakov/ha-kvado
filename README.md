Интеграция личного кабинета КВАДО (KVADO) с Home Assistant
==================================================

> Предоставление информации о текущем состоянии лицевых счетов и связанных с ними счетчиков в Home Assistant

## Установка и настройка

1. В HACS добавить [пользовательский репозиторий](https://hacs.xyz/docs/faq/custom_repositories/)
2. Пройти процедуру аутентификации используя учетные данные [личного кабинета КВАДО](https://cabinet.kvado.ru/login)
3. Выберите необходимые учетные записи. Вложенные в них счетчики будут добавлены автоматически

## Отправка показаний
Необходимые данные:

```entity_id``` entity ID сенсора учетной записи
Список из
```entity_id``` entity ID сенсора счетчика в учетной записи
```newValue``` новые показания счетчика 

> [!IMPORTANT]  
>  API Квадо ожидает, что показания всех счетчиков одного типа (холодная вода, горячая вода) будут отправлены одним запросом. 

Пример:

```yaml
action: kvado.send_meter_readings
data:
  meter_readings:
    - entity_id: sensor.coldwater_no22_6222586
      newValue: 1
    - entity_id: sensor.coldwater_no22_6239179
      newValue: 1
  entity_id: sensor.account_12345
```

## Отладка

```yaml
logger:
  default: info
  logs:
    custom_components.kvado: debug
```