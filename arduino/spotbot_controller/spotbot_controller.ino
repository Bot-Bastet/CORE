/*
 * SpotBot Controller — Arduino Mega v3.2
 * =========================================
 * IMU : BNO085 uniquement (I2C, adresse 0x4A)
 * Servos : 12x MG996R (D2-D13, alim externe 6V/10A)
 * Sonar  : HC-SR04 (TRIG=D22, ECHO=D23) — optionnel
 *
 * NOUVEAUTES v3.2 :
 *   - Limites d'angle individuelles par servo (EEPROM addr 22-219)
 *   - Offsets zero par servo (EEPROM addr 220-317)
 *   - Commandes set_limit / get_limits / set_offset / get_offsets / query
 *   - rx_doc.clear() avant chaque deserializeJson() → fix micro-mouvements
 *   - Offsets appliqués en hardware dans l'Arduino (pas dans le Pi)
 *
 * EEPROM layout :
 *   Addr 0..21    : IMU calibration (magic + q_offset[4] + CRC16) [existant]
 *   Addr 22..69   : servo_min_limit[12] floats (48 bytes)
 *   Addr 70..117  : servo_max_limit[12] floats (48 bytes)
 *   Addr 118..121 : magic limits uint32_t = 0xBEEFCAFE
 *   Addr 122..123 : CRC16 limits
 *   Addr 124..171 : servo_offset[12] floats (48 bytes)
 *   Addr 172..175 : magic offsets uint32_t = 0xDEADF00D
 *   Addr 176..177 : CRC16 offsets
 *
 * JSON emis (20 Hz):
 * {
 *   "imu":{
 *     "qw":10000,"qx":0,"qy":0,"qz":0,  ← quaternion * 10000
 *     "lax":0,"lay":0,"laz":0,           ← accél linéaire cm/s² * 100
 *     "gx":0,"gy":0,"gz":0,              ← gyro mrad/s * 1000
 *     "calib":3                          ← calibration 0-3 (3=parfait)
 *   },
 *   "sonar":{"dist_cm":42.5,"valid":true,"alert":false},
 *   "servos":[90,90,...],               ← 12 angles courants (degrés bruts, sans offset)
 *   "version":"v0.2.21"
 * }
 *
 * JSON recu:
 *   {"servos":[90,90,...]}              (12 angles 0-180°, avant offset)
 *   {"cmd":"stand"}                     (stand | sit | stop | reset_imu | query)
 *   {"cmd":"set_limit","index":i,"min":x,"max":y}
 *   {"cmd":"get_limits"}
 *   {"cmd":"set_offset","index":i,"offset":x}
 *   {"cmd":"get_offsets"}
 *   {"cmd":"query"}                     → répond avec positions + limites + offsets
 *
 * BRANCHEMENTS:
 *   Servos D2-D13  — alim externe 6V/10A (GND commun Arduino)
 *   BNO085 SDA→20, SCL→21, VCC→3.3V, GND, INT→D18, RST→D19
 *             PS0→GND, PS1→GND (adresse 0x4A)
 *   HC-SR04 TRIG→D22, ECHO→D23, VCC→5V, GND
 *
 * LIBRAIRIE:
 *   arduino-cli lib install "SparkFun BNO08x Cortex Based IMU"
 *   arduino-cli lib install "ArduinoJson"
 */

#include <Arduino.h>
#include <Servo.h>
#include <Wire.h>
#include <EEPROM.h>
#include <SparkFun_BNO08x_Arduino_Library.h>
#include <ArduinoJson.h>

// ============================================================
// FIX NATIF : Calibration IMU persistante en EEPROM Arduino
// ============================================================
#define BNO085_CALIB_MAGIC 0xCAFEBABEul
#define EEPROM_CALIB_ADDR   0

struct EepromImuCalib {
    uint32_t magic;
    float qw, qx, qy, qz;
    uint16_t crc;
};
static_assert(sizeof(EepromImuCalib) == 22, "EEPROM layout doit faire 22 octets");

// ============================================================
// Limites servo (EEPROM addr 22-123)
// ============================================================
#define SERVO_LIMITS_MAGIC  0xBEEFCAFEul
#define EEPROM_LIMITS_ADDR  22   // 12×float min (48) + 12×float max (48) + magic(4) + crc(2) = 102 bytes

// ============================================================
// Offsets servo (EEPROM addr 124-177)
// ============================================================
#define SERVO_OFFSETS_MAGIC 0xDEADF00Dul
#define EEPROM_OFFSETS_ADDR 124  // 12×float offset (48) + magic(4) + crc(2) = 54 bytes

// ============================================================
// Inversion servo (EEPROM addr 178-195)
// ============================================================
// Flag par servo : 1 = inversé, 0 = normal
// Quand inversé : angle_physique = (180 - cmd) + offset
// Cela permet aux moteurs "miroir" (côté gauche vs droit) d'avoir la bonne direction
#define SERVO_INVERTS_MAGIC 0xF00DBABEul
#define EEPROM_INVERTS_ADDR 178  // 12×uint8 (12) + magic(4) + crc(2) = 18 bytes

static uint16_t crc16_ccitt(const uint8_t* data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; j++) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

// ============================================================
// Configuration
// ============================================================
#define SKETCH_VERSION    "v0.2.22"
#define NUM_SERVOS        12
#define SERIAL_BAUD       250000
#define IMU_PUBLISH_MS    50      // 20 Hz
#define WATCHDOG_MS       3000
#define JSON_BUFFER_SIZE  512     // augmenté pour get_limits / get_offsets
#define SERVO_SPEED       1.0f    // deg/loop (~50 deg/s a 50Hz)

// ArduinoJson documents (RX et TX séparés)
StaticJsonDocument<512> rx_doc;

// ---- Pins servos (D2-D13) ----
const uint8_t SERVO_PINS[NUM_SERVOS] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13};

// ---- BNO085 ----
#define BNO085_INT_PIN  18
#define BNO085_RST_PIN  19
#define BNO085_ADDR     0x4A

// ---- HC-SR04 ----
#define SONAR_ENABLED false
#define SONAR_TRIG_PIN  22
#define SONAR_ECHO_PIN  23
#define SONAR_ALERT_CM  30.0f
#define SONAR_MAX_CM    400.0f
#define SONAR_MIN_CM    2.0f
#define SONAR_SAMPLES   3

// ---- Positions servo ----
const float SERVO_STAND[NUM_SERVOS] = {90,90,90, 90,90,90, 90,90,90, 90,90,90};
const float SERVO_SIT[NUM_SERVOS]   = {90,120,60, 90,120,60, 90,120,60, 90,120,60};

// ============================================================
// Variables globales
// ============================================================
Servo  servos[NUM_SERVOS];
float  servo_targets[NUM_SERVOS];
float  servo_current[NUM_SERVOS];

// Limites individuelles (chargées depuis EEPROM ou valeur par défaut 0/180)
float  servo_min_limit[NUM_SERVOS];
float  servo_max_limit[NUM_SERVOS];

// Offsets zero par servo (chargés depuis EEPROM ou 0.0)
float  servo_offset[NUM_SERVOS];

// Flag d'inversion par servo (chargé depuis EEPROM ou false)
// Quand true : angle physique = (180 - cmd) + offset
bool   servo_inverted[NUM_SERVOS];
bool   offsets_calibrated = false;
bool   limits_calibrated = false;

char   json_buf[JSON_BUFFER_SIZE];
int    json_pos = 0;
bool   bno_ok   = false;

unsigned long last_cmd_ms  = 0;
unsigned long last_imu_ms  = 0;
bool          watchdog_mode  = false;
bool          servos_enabled = false;
bool          flag_capture_initial_pose = false;

// FIX NATIF v6 : save distribué IMU EEPROM
uint8_t       save_buf[22];
volatile uint8_t save_index = 0;

// Save distribué pour les limites (102 bytes → 102 iterations)
uint8_t        limits_save_buf[102];
volatile uint8_t limits_save_index = 0;

// Save distribué pour les offsets (54 bytes → 54 iterations)
uint8_t        offsets_save_buf[54];
volatile uint8_t offsets_save_index = 0;

// Save distribué pour les inversions (18 bytes → 18 iterations)
uint8_t        inverts_save_buf[18];
volatile uint8_t inverts_save_index = 0;

BNO08x bno;

struct BnoData {
    float qw = 1, qx = 0, qy = 0, qz = 0;
    float q_offset_w = 1, q_offset_x = 0, q_offset_y = 0, q_offset_z = 0;
    float lax = 0, lay = 0, laz = 0;
    float gx = 0,  gy = 0,  gz = 0;
    uint8_t calib = 0;
} bno_data;

float sonar_history[SONAR_SAMPLES] = {0};
int   sonar_idx   = 0;
bool  sonar_valid = false;
unsigned long last_sonar_ms = 0;
float cached_sonar_dist = -1.0f;

// ============================================================
// Prototypes
// ============================================================
void load_calibration_from_eeprom();
void save_calibration_init();
void load_limits_from_eeprom();
void save_limits_init();
void load_offsets_from_eeprom();
void save_offsets_init();
void load_inverts_from_eeprom();
void save_inverts_init();
void resetBNO085();
void clear_calibration_from_eeprom();
void setStand();
void setSit();
void stopServos();
void applyServos();
void readSerial();
void parseJSON(const char* json);
void readBNO085();
void publishAll(float dist_cm);
void publishQuery();
float readSonar();

// ============================================================
// Setup
// ============================================================
void setup() {
    for (int i = 0; i < NUM_SERVOS; i++) {
        pinMode(SERVO_PINS[i], OUTPUT);
        digitalWrite(SERVO_PINS[i], LOW);
        servo_targets[i] = SERVO_STAND[i];
        servo_current[i] = SERVO_STAND[i];
        // Valeurs par défaut — seront écrasées par EEPROM si disponible
        servo_min_limit[i] = 0.0f;
        servo_max_limit[i] = 180.0f;
        servo_offset[i]    = 0.0f;
        servo_inverted[i]  = false;
    }
    delay(50);

    Serial.begin(SERIAL_BAUD);
    delay(100);

    Wire.begin();
    Wire.setClock(400000);

    Serial.println("{\"boot\":\"pre-init\"}");
    Serial.flush();

    // BNO085
    pinMode(BNO085_INT_PIN, INPUT_PULLUP);
    pinMode(BNO085_RST_PIN, OUTPUT);
    digitalWrite(BNO085_RST_PIN, LOW);
    delay(50);
    digitalWrite(BNO085_RST_PIN, HIGH);
    delay(300);

    bno_ok = bno.begin(BNO085_ADDR, Wire);
    if (bno_ok) {
        bno.enableRotationVector(20);
        bno.enableLinearAccelerometer(20);
        bno.enableGyro(20);
        load_calibration_from_eeprom();
        Serial.println("{\"boot\":\"SpotBot v3.2\",\"bno085\":true}");
    } else {
        Serial.println("{\"boot\":\"SpotBot v3.2\",\"bno085\":false,\"error\":\"BNO085 non detecte\"}");
    }

    // Charger limites, offsets et inversions depuis EEPROM
    load_limits_from_eeprom();
    load_offsets_from_eeprom();
    load_inverts_from_eeprom();

    // HC-SR04
    pinMode(SONAR_TRIG_PIN, OUTPUT);
    pinMode(SONAR_ECHO_PIN, INPUT);
    digitalWrite(SONAR_TRIG_PIN, LOW);

    last_cmd_ms = millis();
    Serial.print("{\"status\":\"ready\",\"bno085\":");
    Serial.print(bno_ok ? "true" : "false");
    Serial.println(",\"sonar\":true}");
}

// ============================================================
// Loop
// ============================================================
void loop() {
    readSerial();

    if (!watchdog_mode && (millis() - last_cmd_ms) > WATCHDOG_MS) {
        watchdog_mode = true;
        if (servos_enabled) {
            // 🔴 SAFETY: If motors are not calibrated, DETACH all servos
            // instead of trying to stand. This prevents the robot from
            // moving to a random position when communication is lost
            // and calibration has not been configured.
            if (offsets_calibrated && limits_calibrated) {
                setStand();
                Serial.println("{\"watchdog\":\"stand\"}");
            } else {
                stopServos();
                Serial.println("{\"watchdog\":\"stop_uncalibrated\"}");
            }
        }
    }

    applyServos();

    if (bno_ok) readBNO085();

#if SONAR_ENABLED
    cached_sonar_dist = readSonar();
#else
    cached_sonar_dist = -1.0f;
    sonar_valid = false;
#endif

    if ((millis() - last_imu_ms) >= IMU_PUBLISH_MS) {
        last_imu_ms = millis();
        publishAll(cached_sonar_dist);
    }

    // FIX NATIF v6 : save distribué IMU EEPROM (1 octet/boucle)
    if (save_index > 0 && save_index <= 22) {
        uint8_t idx = save_index - 1;
        EEPROM.update(EEPROM_CALIB_ADDR + idx, save_buf[idx]);
        save_index++;
        if (save_index > 22) {
            save_index = 0;
            Serial.println("{\"info\":\"EEPROM_IMU_PERSISTED\"}");
        }
    }

    // Save distribué limites servo (1 octet/boucle)
    if (limits_save_index > 0 && limits_save_index <= 102) {
        uint8_t idx = limits_save_index - 1;
        EEPROM.update(EEPROM_LIMITS_ADDR + idx, limits_save_buf[idx]);
        limits_save_index++;
        if (limits_save_index > 102) {
            limits_save_index = 0;
            limits_calibrated = true;
            Serial.println("{\"info\":\"EEPROM_LIMITS_PERSISTED\"}");
        }
    }

    // Save distribué offsets servo (1 octet/boucle)
    if (offsets_save_index > 0 && offsets_save_index <= 54) {
        uint8_t idx = offsets_save_index - 1;
        EEPROM.update(EEPROM_OFFSETS_ADDR + idx, offsets_save_buf[idx]);
        offsets_save_index++;
        if (offsets_save_index > 54) {
            offsets_save_index = 0;
            offsets_calibrated = true;
            Serial.println("{\"info\":\"EEPROM_OFFSETS_PERSISTED\"}");
        }
    }

    // Save distribué inversions servo (1 octet/boucle)
    if (inverts_save_index > 0 && inverts_save_index <= 18) {
        uint8_t idx = inverts_save_index - 1;
        EEPROM.update(EEPROM_INVERTS_ADDR + idx, inverts_save_buf[idx]);
        inverts_save_index++;
        if (inverts_save_index > 18) {
            inverts_save_index = 0;
            Serial.println("{\"info\":\"EEPROM_INVERTS_PERSISTED\"}");
        }
    }
}

// ============================================================
// BNO085
// ============================================================
void readBNO085() {
    if (bno.wasReset()) {
        bno.enableRotationVector(20);
        bno.enableLinearAccelerometer(20);
        bno.enableGyro(20);
        Serial.println("{\"warn\":\"BNO085 reset — re-init\"}");
    }

    if (!bno.getSensorEvent()) return;

    switch (bno.getSensorEventID()) {
        case SENSOR_REPORTID_ROTATION_VECTOR: {
            float sqw = bno.getQuatReal();
            float sqx = bno.getQuatI();
            float sqy = bno.getQuatJ();
            float sqz = bno.getQuatK();
            // Rotation 180° autour de X (IMU montée à l'envers)
            float post_w = sqx;
            float post_x = -sqw;
            float post_y = -sqz;
            float post_z = sqy;

            if (flag_capture_initial_pose) {
                flag_capture_initial_pose = false;
                bno_data.q_offset_w = post_w;
                bno_data.q_offset_x = post_x;
                bno_data.q_offset_y = post_y;
                bno_data.q_offset_z = post_z;
                save_calibration_init();
                Serial.print("{\"info\":\"CAPTURED q_offset=[\"");
                Serial.print(post_w, 4); Serial.print(",");
                Serial.print(post_x, 4); Serial.print(",");
                Serial.print(post_y, 4); Serial.print(",");
                Serial.print(post_z, 4); Serial.println("]\"}");
            }

            float oqw =  bno_data.q_offset_w;
            float oqx = -bno_data.q_offset_x;
            float oqy = -bno_data.q_offset_y;
            float oqz = -bno_data.q_offset_z;
            float cal_w = oqw*post_w - oqx*post_x - oqy*post_y - oqz*post_z;
            float cal_x = oqw*post_x + oqx*post_w + oqy*post_z - oqz*post_y;
            float cal_y = oqw*post_y - oqx*post_z + oqy*post_w + oqz*post_x;
            float cal_z = oqw*post_z + oqx*post_y - oqy*post_x + oqz*post_w;
            float nrm = sqrt(cal_w*cal_w + cal_x*cal_x + cal_y*cal_y + cal_z*cal_z);
            if (nrm > 1e-6) {
                cal_w /= nrm; cal_x /= nrm; cal_y /= nrm; cal_z /= nrm;
            }
            bno_data.qw    = cal_w;
            bno_data.qx    = cal_x;
            bno_data.qy    = cal_y;
            bno_data.qz    = cal_z;
            bno_data.calib = bno.getQuatAccuracy();
            break;
        }
        case SENSOR_REPORTID_LINEAR_ACCELERATION:
            bno_data.lax = bno.getLinAccelX();
            bno_data.lay = -bno.getLinAccelY();
            bno_data.laz = -bno.getLinAccelZ();
            break;
        case SENSOR_REPORTID_GYROSCOPE_CALIBRATED:
            bno_data.gx = bno.getGyroX();
            bno_data.gy = -bno.getGyroY();
            bno_data.gz = -bno.getGyroZ();
            break;
    }
}

// ============================================================
// Serial JSON parser
// ============================================================
void readSerial() {
    while (Serial.available() > 0) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (json_pos > 0) {
                json_buf[json_pos] = '\0';
                parseJSON(json_buf);
                json_pos = 0;
            }
        } else if (json_pos < JSON_BUFFER_SIZE - 1) {
            json_buf[json_pos++] = c;
        }
    }
}

void parseJSON(const char* json) {
    int len = strlen(json);
    while (len > 0 && (json[len-1] == ' ' || json[len-1] == '\t' || json[len-1] == '\r')) len--;
    if (len < 2 || json[len-1] != '}') return;

    // FIX v3.2 : clear explicite avant chaque parse → élimine les micro-mouvements
    // causés par des champs résiduels du message précédent dans le document statique.
    rx_doc.clear();

    DeserializationError err = deserializeJson(rx_doc, json, len);
    if (err) return;

    last_cmd_ms   = millis();
    watchdog_mode = false;

    // ── {"servos": [90,90,...]}  (12 angles depuis le Pi, AVANT offset) ──
    JsonArray arr = rx_doc["servos"].as<JsonArray>();
    if (arr.size() == NUM_SERVOS) {
        // Validation du checksum pour prévenir les erreurs de transmission
        if (!rx_doc["chk"].isNull()) {
            int expected_chk = 0;
            for (int i = 0; i < NUM_SERVOS; i++) {
                expected_chk += (int)arr[i].as<float>();
            }
            expected_chk = expected_chk % 1000;
            int actual_chk = rx_doc["chk"].as<int>();
            if (expected_chk != actual_chk) {
                Serial.println("{\"error\":\"servos_chk_failed\"}");
                return;
            }
        }
        
        // Bloquer si non calibré SAUF si c'est explicitement marqué "manual": true
        bool is_manual = false;
        if (!rx_doc["manual"].isNull()) {
            is_manual = rx_doc["manual"].as<bool>();
        }
        if (!is_manual && (!offsets_calibrated || !limits_calibrated)) {
            // Moteurs non calibrés et commande non manuelle -> on ignore silencieusement
            // pour ne pas détacher les servos actifs en mode manuel (EasyConfig / testeur).
            return;
        }
        
        servos_enabled = true;
        for (int i = 0; i < NUM_SERVOS; i++) {
            if (!servos[i].attached()) {
                servos[i].attach(SERVO_PINS[i]);
                servo_current[i] = constrain(arr[i].as<float>(), servo_min_limit[i], servo_max_limit[i]);
            }
            servo_targets[i] = constrain(arr[i].as<float>(), servo_min_limit[i], servo_max_limit[i]);
        }
        return;
    }

    // ── {"cmd": "..."}  ──
    const char* cmd = rx_doc["cmd"].as<const char*>();
    if (!cmd) return;

    if (strcmp(cmd, "stand") == 0) {
        if (!offsets_calibrated || !limits_calibrated) return;
        setStand();
    } else if (strcmp(cmd, "sit") == 0) {
        if (!offsets_calibrated || !limits_calibrated) return;
        setSit();
    } else if (strcmp(cmd, "stop") == 0) {
        stopServos();
    } else if (strcmp(cmd, "reset_imu") == 0) {
        resetBNO085();
    } else if (strcmp(cmd, "clear_calib") == 0) {
        clear_calibration_from_eeprom();
    } else if (strcmp(cmd, "clear_servo_calib") == 0) {
        // Erase magic numbers for offsets and limits in EEPROM to reset calibration state
        EEPROM.write(EEPROM_LIMITS_ADDR + 96, 0);
        EEPROM.write(EEPROM_LIMITS_ADDR + 97, 0);
        EEPROM.write(EEPROM_LIMITS_ADDR + 98, 0);
        EEPROM.write(EEPROM_LIMITS_ADDR + 99, 0);
        EEPROM.write(EEPROM_OFFSETS_ADDR + 48, 0);
        EEPROM.write(EEPROM_OFFSETS_ADDR + 49, 0);
        EEPROM.write(EEPROM_OFFSETS_ADDR + 50, 0);
        EEPROM.write(EEPROM_OFFSETS_ADDR + 51, 0);
        offsets_calibrated = false;
        limits_calibrated = false;
        // 🔴 CRITICAL: Immediately detach all servos to prevent any movement
        // when calibration is cleared. Without this, servos remain attached
        // at their current position and the watchdog could re-activate them.
        stopServos();
        Serial.println("{\"info\":\"Servo calibration cleared, safety interlock active\"}");
    } else if (strcmp(cmd, "heartbeat") == 0) {
        // keep-alive, no-op
    } else if (strcmp(cmd, "attach") == 0) {
        // 🔴 CRITICAL: check calibration before allowing manual attach
        // Only allow if offsets are calibrated OR explicitly marked as manual (servo tester / easyconfig)
        bool is_manual = false;
        if (!rx_doc["manual"].isNull()) {
            is_manual = rx_doc["manual"].as<bool>();
        }
        if (!is_manual && (!offsets_calibrated || !limits_calibrated)) {
            Serial.println("{\"error\":\"attach_blocked_calibration_required\"}");
            return;
        }
        if (!rx_doc["index"].isNull()) {
            int idx = rx_doc["index"].as<int>();
            if (idx >= 0 && idx < NUM_SERVOS) {
                if (!servos[idx].attached()) servos[idx].attach(SERVO_PINS[idx]);
                servos_enabled = true;
            }
        }
    } else if (strcmp(cmd, "detach") == 0) {
        if (!rx_doc["index"].isNull()) {
            int idx = rx_doc["index"].as<int>();
            if (idx >= 0 && idx < NUM_SERVOS) {
                servos[idx].detach();
                pinMode(SERVO_PINS[idx], OUTPUT);
                digitalWrite(SERVO_PINS[idx], LOW);
            }
        }
    } else if (strcmp(cmd, "write") == 0) {
        if (!rx_doc["index"].isNull() && !rx_doc["angle"].isNull()) {
            int idx   = rx_doc["index"].as<int>();
            float ang = rx_doc["angle"].as<float>();
            
            // 🔴 CRITICAL: check calibration before allowing manual servo write
            // Only allow if offsets are calibrated OR explicitly marked as manual
            bool is_manual = false;
            if (!rx_doc["manual"].isNull()) {
                is_manual = rx_doc["manual"].as<bool>();
            }
            if (!is_manual && (!offsets_calibrated || !limits_calibrated)) {
                Serial.println("{\"error\":\"write_blocked_calibration_required\"}");
                return;
            }
            
            // Validation du checksum de sécurité pour éviter les erreurs de transmission
            if (!rx_doc["chk"].isNull()) {
                int expected_chk = (idx + (int)ang) % 100;
                int actual_chk = rx_doc["chk"].as<int>();
                if (expected_chk != actual_chk) {
                    Serial.println("{\"error\":\"write_chk_failed\"}");
                    return;
                }
            }
            
            if (idx >= 0 && idx < NUM_SERVOS) {
                if (!servos[idx].attached()) servos[idx].attach(SERVO_PINS[idx]);
                servo_targets[idx] = constrain(ang, servo_min_limit[idx], servo_max_limit[idx]);
                servos_enabled = true;
            }
        }

    // ── Nouvelles commandes v3.2 ──

    } else if (strcmp(cmd, "set_limit") == 0) {
        // {"cmd":"set_limit","index":i,"min":x,"max":y}
        if (!rx_doc["index"].isNull()) {
            int idx = rx_doc["index"].as<int>();
            if (idx >= 0 && idx < NUM_SERVOS) {
                if (!rx_doc["min"].isNull()) servo_min_limit[idx] = rx_doc["min"].as<float>();
                if (!rx_doc["max"].isNull()) servo_max_limit[idx] = rx_doc["max"].as<float>();
                // Clamp target courant dans les nouvelles limites
                servo_targets[idx] = constrain(servo_targets[idx], servo_min_limit[idx], servo_max_limit[idx]);
                save_limits_init();
                Serial.print("{\"info\":\"limit_set\",\"index\":");
                Serial.print(idx);
                Serial.print(",\"min\":"); Serial.print(servo_min_limit[idx], 1);
                Serial.print(",\"max\":"); Serial.print(servo_max_limit[idx], 1);
                Serial.println("}");
            }
        }

    } else if (strcmp(cmd, "get_limits") == 0) {
        // Répondre avec toutes les limites
        Serial.print("{\"limits\":[");
        for (int i = 0; i < NUM_SERVOS; i++) {
            Serial.print("[");
            Serial.print(servo_min_limit[i], 1);
            Serial.print(",");
            Serial.print(servo_max_limit[i], 1);
            Serial.print("]");
            if (i < NUM_SERVOS - 1) Serial.print(",");
        }
        Serial.println("]}");

    } else if (strcmp(cmd, "set_offset") == 0) {
        // {"cmd":"set_offset","index":i,"offset":x}
        if (!rx_doc["index"].isNull() && !rx_doc["offset"].isNull()) {
            int idx    = rx_doc["index"].as<int>();
            float off  = rx_doc["offset"].as<float>();
            if (idx >= 0 && idx < NUM_SERVOS) {
                servo_offset[idx] = off;
                save_offsets_init();
                Serial.print("{\"info\":\"offset_set\",\"index\":");
                Serial.print(idx);
                Serial.print(",\"offset\":"); Serial.print(off, 2);
                Serial.println("}");
            }
        }

    } else if (strcmp(cmd, "get_offsets") == 0) {
        Serial.print("{\"offsets\":[");
        for (int i = 0; i < NUM_SERVOS; i++) {
            Serial.print(servo_offset[i], 2);
            if (i < NUM_SERVOS - 1) Serial.print(",");
        }
        Serial.println("]}");

    } else if (strcmp(cmd, "set_invert") == 0) {
        // {"cmd":"set_invert","index":i,"inverted":true/false}
        if (!rx_doc["index"].isNull() && !rx_doc["inverted"].isNull()) {
            int idx  = rx_doc["index"].as<int>();
            bool inv = rx_doc["inverted"].as<bool>();
            if (idx >= 0 && idx < NUM_SERVOS) {
                servo_inverted[idx] = inv;
                save_inverts_init();
                Serial.print("{\"info\":\"invert_set\",\"index\":");
                Serial.print(idx);
                Serial.print(",\"inverted\":"); Serial.print(inv ? "true" : "false");
                Serial.println("}");
            }
        }

    } else if (strcmp(cmd, "get_inverts") == 0) {
        Serial.print("{\"inverted\":[");
        for (int i = 0; i < NUM_SERVOS; i++) {
            Serial.print(servo_inverted[i] ? "true" : "false");
            if (i < NUM_SERVOS - 1) Serial.print(",");
        }
        Serial.println("]}");

    } else if (strcmp(cmd, "query") == 0) {
        publishQuery();
    }
}

// ============================================================
// EEPROM — IMU calibration
// ============================================================
void save_calibration_init() {
    save_buf[0] = (uint8_t)(BNO085_CALIB_MAGIC         & 0xFF);
    save_buf[1] = (uint8_t)((BNO085_CALIB_MAGIC >>  8) & 0xFF);
    save_buf[2] = (uint8_t)((BNO085_CALIB_MAGIC >> 16) & 0xFF);
    save_buf[3] = (uint8_t)((BNO085_CALIB_MAGIC >> 24) & 0xFF);
    memcpy(save_buf +  4, &bno_data.q_offset_w, 4);
    memcpy(save_buf +  8, &bno_data.q_offset_x, 4);
    memcpy(save_buf + 12, &bno_data.q_offset_y, 4);
    memcpy(save_buf + 16, &bno_data.q_offset_z, 4);
    uint16_t crc = crc16_ccitt(save_buf, 20);
    save_buf[20] = (uint8_t)(crc & 0xFF);
    save_buf[21] = (uint8_t)((crc >> 8) & 0xFF);
    save_index = 1;
}

void load_calibration_from_eeprom() {
    uint8_t buf[22];
    for (uint8_t i = 0; i < 22; i++) buf[i] = EEPROM.read(EEPROM_CALIB_ADDR + i);
    uint32_t magic = (uint32_t)buf[0] | ((uint32_t)buf[1] << 8)
                   | ((uint32_t)buf[2] << 16) | ((uint32_t)buf[3] << 24);
    if (magic != BNO085_CALIB_MAGIC) {
        bno_data.q_offset_w = 1.0f; bno_data.q_offset_x = 0.0f;
        bno_data.q_offset_y = 0.0f; bno_data.q_offset_z = 0.0f;
        return;
    }
    uint16_t expected = crc16_ccitt(buf, 20);
    uint16_t stored   = (uint16_t)buf[20] | ((uint16_t)buf[21] << 8);
    if (expected != stored) {
        Serial.println("{\"warn\":\"BNO085 EEPROM calibration CRC invalid - fallback identity\"}");
        bno_data.q_offset_w = 1.0f; bno_data.q_offset_x = 0.0f;
        bno_data.q_offset_y = 0.0f; bno_data.q_offset_z = 0.0f;
        return;
    }
    memcpy(&bno_data.q_offset_w, buf +  4, 4);
    memcpy(&bno_data.q_offset_x, buf +  8, 4);
    memcpy(&bno_data.q_offset_y, buf + 12, 4);
    memcpy(&bno_data.q_offset_z, buf + 16, 4);
    Serial.print("{\"info\":\"EEPROM_IMU_LOADED\"}");
}

void resetBNO085() {
    flag_capture_initial_pose = true;
    Serial.println("{\"info\":\"RESET_IMU_CMD_RECEIVED\"}");
}

void clear_calibration_from_eeprom() {
    bno_data.q_offset_w = 1.0f; bno_data.q_offset_x = 0.0f;
    bno_data.q_offset_y = 0.0f; bno_data.q_offset_z = 0.0f;
    save_calibration_init();
    Serial.println("{\"info\":\"BNO085 EEPROM calibration cleared\"}");
}

// ============================================================
// EEPROM — Limites servo (48+48+4+2 = 102 bytes)
// ============================================================
// Format save_buf : min[12×4] + max[12×4] + magic[4] + crc[2]
void save_limits_init() {
    for (int i = 0; i < 12; i++) memcpy(limits_save_buf + i*4,      &servo_min_limit[i], 4);
    for (int i = 0; i < 12; i++) memcpy(limits_save_buf + 48 + i*4, &servo_max_limit[i], 4);
    uint32_t magic = SERVO_LIMITS_MAGIC;
    memcpy(limits_save_buf + 96, &magic, 4);
    uint16_t crc = crc16_ccitt(limits_save_buf, 100);
    limits_save_buf[100] = (uint8_t)(crc & 0xFF);
    limits_save_buf[101] = (uint8_t)((crc >> 8) & 0xFF);
    limits_save_index = 1;
}

void load_limits_from_eeprom() {
    uint8_t buf[102];
    for (int i = 0; i < 102; i++) buf[i] = EEPROM.read(EEPROM_LIMITS_ADDR + i);
    uint32_t magic;
    memcpy(&magic, buf + 96, 4);
    if (magic != SERVO_LIMITS_MAGIC) {
        // Pas encore configuré → valeurs par défaut (0/180), déjà initialisées dans setup()
        limits_calibrated = false;
        Serial.println("{\"info\":\"EEPROM_LIMITS: no saved data, using defaults 0/180\"}");
        return;
    }
    uint16_t expected = crc16_ccitt(buf, 100);
    uint16_t stored   = (uint16_t)buf[100] | ((uint16_t)buf[101] << 8);
    if (expected != stored) {
        limits_calibrated = false;
        Serial.println("{\"warn\":\"EEPROM_LIMITS CRC invalid - fallback defaults\"}");
        return;
    }
    for (int i = 0; i < 12; i++) memcpy(&servo_min_limit[i], buf + i*4,      4);
    for (int i = 0; i < 12; i++) memcpy(&servo_max_limit[i], buf + 48 + i*4, 4);
    limits_calibrated = true;
    Serial.println("{\"info\":\"EEPROM_LIMITS_LOADED\"}");
}

// ============================================================
// EEPROM — Offsets servo (48+4+2 = 54 bytes)
// ============================================================
void save_offsets_init() {
    for (int i = 0; i < 12; i++) memcpy(offsets_save_buf + i*4, &servo_offset[i], 4);
    uint32_t magic = SERVO_OFFSETS_MAGIC;
    memcpy(offsets_save_buf + 48, &magic, 4);
    uint16_t crc = crc16_ccitt(offsets_save_buf, 52);
    offsets_save_buf[52] = (uint8_t)(crc & 0xFF);
    offsets_save_buf[53] = (uint8_t)((crc >> 8) & 0xFF);
    offsets_save_index = 1;
}

void load_offsets_from_eeprom() {
    uint8_t buf[54];
    for (int i = 0; i < 54; i++) buf[i] = EEPROM.read(EEPROM_OFFSETS_ADDR + i);
    uint32_t magic;
    memcpy(&magic, buf + 48, 4);
    if (magic != SERVO_OFFSETS_MAGIC) {
        offsets_calibrated = false;
        Serial.println("{\"info\":\"EEPROM_OFFSETS: no saved data, using zeros\"}");
        return;
    }
    uint16_t expected = crc16_ccitt(buf, 52);
    uint16_t stored   = (uint16_t)buf[52] | ((uint16_t)buf[53] << 8);
    if (expected != stored) {
        offsets_calibrated = false;
        Serial.println("{\"warn\":\"EEPROM_OFFSETS CRC invalid - fallback zeros\"}");
        return;
    }
    for (int i = 0; i < 12; i++) memcpy(&servo_offset[i], buf + i*4, 4);
    offsets_calibrated = true;
    Serial.println("{\"info\":\"EEPROM_OFFSETS_LOADED\"}");
}

// ============================================================
// EEPROM — Inversions servo (12+4+2 = 18 bytes)
// ============================================================
// Format : inverted[12]×uint8 + magic[4] + crc[2]
void save_inverts_init() {
    for (int i = 0; i < 12; i++) inverts_save_buf[i] = servo_inverted[i] ? 1 : 0;
    uint32_t magic = SERVO_INVERTS_MAGIC;
    memcpy(inverts_save_buf + 12, &magic, 4);
    uint16_t crc = crc16_ccitt(inverts_save_buf, 16);
    inverts_save_buf[16] = (uint8_t)(crc & 0xFF);
    inverts_save_buf[17] = (uint8_t)((crc >> 8) & 0xFF);
    inverts_save_index = 1;
}

void load_inverts_from_eeprom() {
    uint8_t buf[18];
    for (int i = 0; i < 18; i++) buf[i] = EEPROM.read(EEPROM_INVERTS_ADDR + i);
    uint32_t magic;
    memcpy(&magic, buf + 12, 4);
    if (magic != SERVO_INVERTS_MAGIC) {
        Serial.println("{\"info\":\"EEPROM_INVERTS: no saved data, all normal\"}");
        return;
    }
    uint16_t expected = crc16_ccitt(buf, 16);
    uint16_t stored   = (uint16_t)buf[16] | ((uint16_t)buf[17] << 8);
    if (expected != stored) {
        Serial.println("{\"warn\":\"EEPROM_INVERTS CRC invalid - fallback normal\"}");
        return;
    }
    for (int i = 0; i < 12; i++) servo_inverted[i] = (buf[i] != 0);
    Serial.println("{\"info\":\"EEPROM_INVERTS_LOADED\"}");
}

// ============================================================
// Postures
// ============================================================
void setStand() {
    if (!offsets_calibrated || !limits_calibrated) return;
    servos_enabled = true;
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (!servos[i].attached()) servos[i].attach(SERVO_PINS[i]);
        servo_targets[i] = constrain(SERVO_STAND[i], servo_min_limit[i], servo_max_limit[i]);
    }
    Serial.println("{\"info\":\"stand\"}");
}
void setSit() {
    if (!offsets_calibrated || !limits_calibrated) return;
    servos_enabled = true;
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (!servos[i].attached()) servos[i].attach(SERVO_PINS[i]);
        servo_targets[i] = constrain(SERVO_SIT[i], servo_min_limit[i], servo_max_limit[i]);
    }
    Serial.println("{\"info\":\"sit\"}");
}
void stopServos() {
    servos_enabled = false;
    for (int i = 0; i < NUM_SERVOS; i++) {
        servos[i].detach();
        pinMode(SERVO_PINS[i], OUTPUT);
        digitalWrite(SERVO_PINS[i], LOW);
    }
    Serial.println("{\"info\":\"servos_stopped\"}");
}

// ============================================================
// Application des servos avec offset + inversion hardware
// ============================================================
// Ordre d'application :
//   1. Interpolation vers la cible (SERVO_SPEED deg/loop)
//   2. Inversion miroir si servo_inverted[i] = true : angle_inv = 180 - angle_courant
//   3. Ajout de l'offset de calibration zéro
//   4. Constrain dans [min_limit, max_limit]
// Ainsi les commandes du Pi/URDF travaillent toujours dans un espace logique
// cohérent (0-180° avec 90° = neutre) et l'Arduino corrige en hardware.
void applyServos() {
    if (!servos_enabled) return;
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (servos[i].attached()) {
            float diff = servo_targets[i] - servo_current[i];
            if (diff <= SERVO_SPEED && diff >= -SERVO_SPEED) {
                servo_current[i] = servo_targets[i];
            } else {
                servo_current[i] += (diff > 0.0f ? SERVO_SPEED : -SERVO_SPEED);
            }
            // Inversion miroir (côté gauche/droit)
            float logical = servo_inverted[i] ? (180.0f - servo_current[i]) : servo_current[i];
            // Offset de calibration zéro
            float physical = constrain(logical + servo_offset[i],
                                       servo_min_limit[i], servo_max_limit[i]);
            servos[i].write((int)physical);
        }
    }
}

// ============================================================
// HC-SR04
// ============================================================
float readSonar() {
    digitalWrite(SONAR_TRIG_PIN, LOW);  delayMicroseconds(2);
    digitalWrite(SONAR_TRIG_PIN, HIGH); delayMicroseconds(10);
    digitalWrite(SONAR_TRIG_PIN, LOW);
    long dur = pulseIn(SONAR_ECHO_PIN, HIGH, 11600UL);
    if (dur == 0) { sonar_valid = false; return -1.0f; }
    float d = dur / 58.0f;
    if (d < SONAR_MIN_CM || d > SONAR_MAX_CM) { sonar_valid = false; return -1.0f; }
    sonar_history[sonar_idx] = d;
    sonar_idx = (sonar_idx + 1) % SONAR_SAMPLES;
    sonar_valid = true;
    float sum = 0;
    for (int i = 0; i < SONAR_SAMPLES; i++) sum += sonar_history[i];
    return sum / SONAR_SAMPLES;
}

// ============================================================
// Publication JSON principale (20 Hz)
// ============================================================
void publishAll(float dist_cm) {
    bool alert = sonar_valid && (dist_cm > 0) && (dist_cm < SONAR_ALERT_CM);

    Serial.print("{\"imu\":{");
    Serial.print("\"qw\":"); Serial.print((int16_t)(bno_data.qw * 10000));
    Serial.print(",\"qx\":"); Serial.print((int16_t)(bno_data.qx * 10000));
    Serial.print(",\"qy\":"); Serial.print((int16_t)(bno_data.qy * 10000));
    Serial.print(",\"qz\":"); Serial.print((int16_t)(bno_data.qz * 10000));
    Serial.print(",\"lax\":"); Serial.print((int16_t)(bno_data.lax * 100));
    Serial.print(",\"lay\":"); Serial.print((int16_t)(bno_data.lay * 100));
    Serial.print(",\"laz\":"); Serial.print((int16_t)(bno_data.laz * 100));
    Serial.print(",\"gx\":"); Serial.print((int16_t)(bno_data.gx * 1000));
    Serial.print(",\"gy\":"); Serial.print((int16_t)(bno_data.gy * 1000));
    Serial.print(",\"gz\":"); Serial.print((int16_t)(bno_data.gz * 1000));
    Serial.print(",\"calib\":"); Serial.print(bno_data.calib);
    Serial.print("},\"sonar\":{");
    Serial.print("\"dist_cm\":"); Serial.print(dist_cm, 1);
    Serial.print(",\"valid\":"); Serial.print(sonar_valid ? "true" : "false");
    Serial.print(",\"alert\":"); Serial.print(alert ? "true" : "false");
    // Inclure les positions courantes des servos (angle logique, sans offset)
    Serial.print("},\"servos\":[");
    for (int i = 0; i < NUM_SERVOS; i++) {
        Serial.print((int)servo_current[i]);
        if (i < NUM_SERVOS - 1) Serial.print(",");
    }
    Serial.print("],\"version\":\"");
    Serial.print(SKETCH_VERSION);
    Serial.println("\"}");
}

// ============================================================
// Réponse query — état complet
// ============================================================
void publishQuery() {
    Serial.print("{\"query\":{\"servos\":[");
    for (int i = 0; i < NUM_SERVOS; i++) {
        Serial.print((int)servo_current[i]);
        if (i < NUM_SERVOS - 1) Serial.print(",");
    }
    Serial.print("],\"targets\":[");
    for (int i = 0; i < NUM_SERVOS; i++) {
        Serial.print((int)servo_targets[i]);
        if (i < NUM_SERVOS - 1) Serial.print(",");
    }
    Serial.print("],\"offsets\":[");
    for (int i = 0; i < NUM_SERVOS; i++) {
        Serial.print(servo_offset[i], 2);
        if (i < NUM_SERVOS - 1) Serial.print(",");
    }
    Serial.print("],\"limits\":[");
    for (int i = 0; i < NUM_SERVOS; i++) {
        Serial.print("[");
        Serial.print(servo_min_limit[i], 1);
        Serial.print(",");
        Serial.print(servo_max_limit[i], 1);
        Serial.print("]");
        if (i < NUM_SERVOS - 1) Serial.print(",");
    }
    Serial.print("],\"enabled\":"); Serial.print(servos_enabled ? "true" : "false");
    Serial.println("}}");
}
