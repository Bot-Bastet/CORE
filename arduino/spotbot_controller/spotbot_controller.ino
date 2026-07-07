/*
 * SpotBot Controller — Arduino Mega v3.1
 * =========================================
 * IMU : BNO085 uniquement (I2C, adresse 0x4A)
 * Servos : 12x MG996R (D2-D13, alim externe 6V/10A)
 * Sonar  : HC-SR04 (TRIG=D22, ECHO=D23) — optionnel
 *
 * JSON emis (50 Hz):
 * {
 *   "imu":{
 *     "qw":10000,"qx":0,"qy":0,"qz":0,  ← quaternion * 10000
 *     "lax":0,"lay":0,"laz":0,           ← accél linéaire cm/s² * 100
 *     "gx":0,"gy":0,"gz":0,              ← gyro mrad/s * 1000
 *     "calib":3                          ← calibration 0-3 (3=parfait)
 *   },
 *   "sonar":{"dist_cm":42.5,"valid":true,"alert":false}
 * }
 *
 * JSON recu:
 *   {"servos":[90,90,...]}   (12 angles 0-180°)
 *   {"cmd":"stand"}          (stand | sit | stop | reset_imu)
 *
 * BRANCHEMENTS:
 *   Servos D2-D13  — alim externe 6V/10A (GND commun Arduino)
 *   BNO085 SDA→20, SCL→21, VCC→3.3V, GND, INT→D18, RST→D19
 *             PS0→GND, PS1→GND (adresse 0x4A)
 *   HC-SR04 TRIG→D22, ECHO→D23, VCC→5V, GND
 *
 * LIBRAIRIE:
 *   arduino-cli lib install "SparkFun BNO08x"
 */

#include <Arduino.h>
#include <Servo.h>
#include <Wire.h>
#include <EEPROM.h>          // FIX NATIF: persistance de la calibration IMU en EEPROM
#include <SparkFun_BNO08x_Arduino_Library.h>

// ============================================================
// FIX NATIF : Calibration IMU persistante en EEPROM Arduino
// ============================================================
// Layout EEPROM Mega2560 (22 octets à l'adresse 0).
// IMPORTANT : `crc` est en DERNIER (packed) — comme ça `offsetof(EepromImuCalib, crc)`
// donne 20 (sizeof de magic + 4 floats), donc le CRC couvre magic + q_offset ensemble,
// pas juste le magic. Sinon une corruption des floats passerait silencieusement.
//   Adr 0  ..3   : magic uint32_t  = BNO085_CALIB_MAGIC  (detecte "calibree" vs usine)
//   Adr 4  ..19  : q_offset_wxyz  float[4]  (pose post-X180 capturee lors de reset_imu)
//   Adr 20 ..21  : crc   uint16_t  CRC16-CCITT sur magic+q_offset
// ============================================================
#define BNO085_CALIB_MAGIC 0xCAFEBABEul
#define EEPROM_CALIB_ADDR   0

// FIX NATIF v4 : on retire __attribute__((packed)) — sur AVR gcc, le qualifier
// packed + EEPROM.put() (template avec reference) peut declencher un bug
// silencieux du compilateur qui ecrit partiellement les 22 octets vers
// l'EEPROM. On evite completement la voie templatee en faisant des
// EEPROM.write() octet par octet, avec un buffer RAM uint8_t[22] dont on
// calcule le CRC16 a la fin. Aussi v4 utilise un signed-magic 0xCAFEBABE
// inversé (plus distinctif que BEEFCAFE pour detecter des coupures) :
//   magic bytes little-endian EF BE CA FE → uint32 = 0xCAFEBABE.
struct EepromImuCalib {
    uint32_t magic;
    float qw, qx, qy, qz;   // q_offset persistant (conjugue de la pose post-X180 capturee)
    uint16_t crc;            // CRC16-CCITT sur magic + q_offset
};
static_assert(sizeof(EepromImuCalib) == 22, "EEPROM layout doit faire 22 octets");

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
#define SKETCH_VERSION    "v0.2.19"
#define NUM_SERVOS        12
#define SERIAL_BAUD       500000
#define IMU_PUBLISH_MS    50      // 20 Hz
#define WATCHDOG_MS       3000
#define JSON_BUFFER_SIZE  320
#define SERVO_SPEED       1.0f    // deg/loop (~50 deg/s a 50Hz) — bon compromis visuel/securite

// ---- Pins servos (D2-D13) ----
const uint8_t SERVO_PINS[NUM_SERVOS] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13};

// ---- BNO085 ----
#define BNO085_INT_PIN  18
#define BNO085_RST_PIN  19
#define BNO085_ADDR     0x4A

// ---- HC-SR04 ----
// Sonar optionnel — activable via #define
#define SONAR_ENABLED false   // Mettre a true si le HC-SR04 est installe
#define SONAR_TRIG_PIN  22
#define SONAR_ECHO_PIN  23
#define SONAR_ALERT_CM  30.0f
#define SONAR_MAX_CM    400.0f
#define SONAR_MIN_CM    2.0f
#define SONAR_SAMPLES   3

// ---- Positions servo ----
const float SERVO_STAND[NUM_SERVOS] = {90,90,90, 90,90,90, 90,90,90, 90,90,90};
const float SERVO_SIT[NUM_SERVOS]   = {90,120,60, 90,120,60, 90,120,60, 90,120,60};
#define SERVO_MIN 0
#define SERVO_MAX 180

// ============================================================
// Variables globales
// ============================================================
Servo  servos[NUM_SERVOS];
float  servo_targets[NUM_SERVOS];
float  servo_current[NUM_SERVOS];
char   json_buf[JSON_BUFFER_SIZE];
int    json_pos = 0;
bool   bno_ok   = false;

unsigned long last_cmd_ms  = 0;
unsigned long last_imu_ms  = 0;
bool          watchdog_mode = false;
bool          servos_enabled = false;  // servos désactivés jusqu'à la 1ère commande
bool          flag_capture_initial_pose = false;  // FIX NATIF: armé par resetBNO085(), consommé par readBNO085()

// FIX NATIF v6 : save distribué sur 22 loop() iterations (1 octet / itération).
// Chaque EEPROM.write(~3.3ms) est entrecoupé d'une boucle entiere
// (readSerial -> applyServos -> readBNO085 -> publishAll) donc le chip n'est JAMAIS
// bloqué >3.3ms consecutifs, ce qui etait la cause des coupures brownout
// mi-écriture qui laissaient les octets 8+ a 0xFF.
uint8_t       save_buf[22];
volatile uint8_t save_index = 0;  // 0 = inactif, 1..22 = octet en cours, >22 = terminé

BNO08x bno;

struct BnoData {
    float qw = 1, qx = 0, qy = 0, qz = 0;
    float q_offset_w = 1, q_offset_x = 0, q_offset_y = 0, q_offset_z = 0;  // FIX NATIF
    float lax = 0, lay = 0, laz = 0;
    float gx = 0,  gy = 0,  gz = 0;
    uint8_t calib = 0;
} bno_data;

// Filtre sonar
float sonar_history[SONAR_SAMPLES] = {0};
int   sonar_idx   = 0;
bool  sonar_valid = false;
unsigned long last_sonar_ms = 0;
float cached_sonar_dist = -1.0f;

// ============================================================
// Setup
// ============================================================
void setup() {
    // ⚠️ URGENCE : forcer TOUS les pins servos à LOW IMMÉDIATEMENT
    // pour éviter tout twitching parasite pendant le boot.
    for (int i = 0; i < NUM_SERVOS; i++) {
        pinMode(SERVO_PINS[i], OUTPUT);
        digitalWrite(SERVO_PINS[i], LOW);
        servo_targets[i] = SERVO_STAND[i];
        servo_current[i] = SERVO_STAND[i];
    }
    delay(50);  // Stabilisation des signaux avant d'initialiser le reste

    Serial.begin(SERIAL_BAUD);
    delay(100);

    // I2C — 400 kHz Fast Mode
    Wire.begin();
    Wire.setClock(400000);

    // print debug
    Serial.println("{\"boot\":\"pre-init\"}");
    Serial.flush();

    // BNO085
    pinMode(BNO085_INT_PIN, INPUT_PULLUP);
    pinMode(BNO085_RST_PIN, OUTPUT);

    // Hardware reset du BNO085 au démarrage pour éviter les freezes I2C
    digitalWrite(BNO085_RST_PIN, LOW);
    delay(50);
    digitalWrite(BNO085_RST_PIN, HIGH);
    delay(300);

    bno_ok = bno.begin(BNO085_ADDR, Wire);
    if (bno_ok) {
        bno.enableRotationVector(20);
        bno.enableLinearAccelerometer(20);
        bno.enableGyro(20);
        load_calibration_from_eeprom();  // FIX NATIF : charge l'offset persistant depuis EEPROM
        Serial.println("{\"boot\":\"SpotBot v3.1\",\"bno085\":true}");
    } else {
        Serial.println("{\"boot\":\"SpotBot v3.1\",\"bno085\":false,\"error\":\"BNO085 non detecte — verifiez I2C et adresse 0x4A\"}");
    }

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
        // Servos déjà libres si jamais activés, sinon on remet en stand
        if (servos_enabled) {
            setStand();
            Serial.println("{\"watchdog\":\"stand\"}");
        }
    }

    applyServos();

    if (bno_ok) readBNO085();

#if SONAR_ENABLED
    // Sonar actif — lire et filtrer
    cached_sonar_dist = readSonar();
#else
    cached_sonar_dist = -1.0f;
    sonar_valid = false;
#endif

    if ((millis() - last_imu_ms) >= IMU_PUBLISH_MS) {
        last_imu_ms = millis();
        publishAll(cached_sonar_dist);
    }

    // FIX NATIF v6 : save distribué — 1 octet EEPROM.write par boucle.
    // write_byte(): 1 octet + delay(0)-equivalent (rien), dure ~3.3ms.
    if (save_index > 0 && save_index <= 22) {
        uint8_t idx = save_index - 1;
        EEPROM.update(EEPROM_CALIB_ADDR + idx, save_buf[idx]);
        save_index++;
        if (save_index > 22) {
            save_index = 0;  // termine
            Serial.println("{\"info\":\"EEPROM_PERSISTED\"}");
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
            // Appliquer une rotation de 180° autour de X (IMU montée à l'envers sous le robot)
            float post_w = sqx;
            float post_x = -sqw;
            float post_y = -sqz;
            float post_z = sqy;

            // FIX NATIF : si resetBNO085() vient d'armer la capture, ancrer la pose
            // post-X180 comme nouvel offset (avant le produit q_offset^-1 * q_post,
            // sinon on composerait deux fois le meme quaternion et la calibration
            // dériverait à chaque clic reset_imu).
            if (flag_capture_initial_pose) {
                flag_capture_initial_pose = false;
                bno_data.q_offset_w = post_w;
                bno_data.q_offset_x = post_x;
                bno_data.q_offset_y = post_y;
                bno_data.q_offset_z = post_z;
                // FIX NATIF v6 : init save_buf + save_index=1, la save reelle
                // est faite en 22 iterations de loop() (1 octet / iter).
                save_calibration_init();
                Serial.print("{\"info\":\"CAPTURED q_offset=[\"");
                Serial.print(post_w, 4); Serial.print(",");
                Serial.print(post_x, 4); Serial.print(",");
                Serial.print(post_y, 4); Serial.print(",");
                Serial.print(post_z, 4); Serial.println("]\"}");
            }

            // FIX NATIF : appliquer q_offset^-1 * q_post (produit de Hamilton, ROS conv).
            // Pour un quaternion unitaire, l'inverse = le conjugue (qw, -qx, -qy, -qz).
            float oqw = bno_data.q_offset_w;
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
    while (len > 0 && (json[len - 1] == ' ' || json[len - 1] == '\t' || json[len - 1] == '\r' || json[len - 1] == '\n')) {
        len--;
    }
    if (len < 2 || json[len - 1] != '}') {
        return;
    }
    last_cmd_ms   = millis();
    watchdog_mode = false;
    if (strstr(json, "\"servos\""))       parseServos(json);
    else if (strstr(json, "\"cmd\""))     parseCmd(json);
}

void parseServos(const char* json) {
    const char* s = strchr(json, '[');
    if (!s) return;
    float angles[NUM_SERVOS]; int n = 0;
    char* p = (char*)(s + 1);
    while (n < NUM_SERVOS && *p && *p != ']') {
        while (*p == ' ' || *p == ',') p++;
        if (*p == ']') break;
        angles[n++] = atof(p);
        while (*p && *p != ',' && *p != ']') p++;
    }
    if (n == NUM_SERVOS) {
        servos_enabled = true;
        for (int i = 0; i < NUM_SERVOS; i++) {
            if (!servos[i].attached()) {
                servos[i].attach(SERVO_PINS[i]);
                servo_current[i] = constrain(angles[i], SERVO_MIN, SERVO_MAX);
            }
            servo_targets[i] = constrain(angles[i], SERVO_MIN, SERVO_MAX);
        }
    }
}

float parseNumAfterKey(const char* json, const char* key) {
    const char* p = strstr(json, key);
    if (!p) return -999.0f;
    p += strlen(key);
    while (*p && (*p == ' ' || *p == ':' || *p == '"' || *p == '\t')) {
        p++;
    }
    return atof(p);
}

void parseCmd(const char* json) {
    if (strstr(json, "\"stand\""))          setStand();
    else if (strstr(json, "\"sit\""))       setSit();
    else if (strstr(json, "\"stop\""))      stopServos();
    else if (strstr(json, "\"reset_imu\"")) resetBNO085();
    else if (strstr(json, "\"clear_calib\"")) clear_calibration_from_eeprom();  // FIX NATIF
    else if (strstr(json, "\"attach\"")) {
        float val = parseNumAfterKey(json, "\"index\"");
        if (val != -999.0f) {
            int idx = (int)val;
            if (idx >= 0 && idx < NUM_SERVOS) {
                if (!servos[idx].attached()) servos[idx].attach(SERVO_PINS[idx]);
                servos_enabled = true;
            }
        }
    }
    else if (strstr(json, "\"detach\"")) {
        float val = parseNumAfterKey(json, "\"index\"");
        if (val != -999.0f) {
            int idx = (int)val;
            if (idx >= 0 && idx < NUM_SERVOS) {
                servos[idx].detach();
                pinMode(SERVO_PINS[idx], OUTPUT);
                digitalWrite(SERVO_PINS[idx], LOW);
            }
        }
    }
    else if (strstr(json, "\"write\"")) {
        float idx_val = parseNumAfterKey(json, "\"index\"");
        float ang_val = parseNumAfterKey(json, "\"angle\"");
        if (idx_val != -999.0f && ang_val != -999.0f) {
            int idx = (int)idx_val;
            float ang = ang_val;
            if (idx >= 0 && idx < NUM_SERVOS) {
                if (!servos[idx].attached()) servos[idx].attach(SERVO_PINS[idx]);
                servo_targets[idx] = constrain(ang, SERVO_MIN, SERVO_MAX);
                servos_enabled = true;
            }
        }
    }
}

// ============================================================
// FIX NATIF : persistance + capture non-bloquante de la calibration IMU
// ============================================================
// FIX NATIF v6 : prepare le buffer de persistance + arme la save distribuee.
// loop() ecrit 1 octet / iteration sur 22 iterations (1 EEPROM.update de 3.3ms
// reparti sur ~440ms via le cadencement naturel de loop()). 16+ms libres entre
// chaque strobe pour les ISRs BNO085/Wire/Serial -> fini le brownout BNO085
// mi-ecriture ; le CRC protege l'integrite des 22 octets a la lecture.
// Format save_buf : magic[4] + q_offset_wxyz[16] + crc16[2], little-endian.
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
    save_index = 1;  // loop() ecrit byte[0] a la 1re iteration puis ++
}

// FIX NATIF v4 : evite EEPROM.get( struct ) pour la meme raison que save.
// Lecture byte by byte dans un buffer RAM, puis memcpy des floats vers la
// struct BnoData. Simple, deterministe, pas de packed template issue.
void load_calibration_from_eeprom() {
    uint8_t buf[22];
    for (uint8_t i = 0; i < 22; i++) {
        buf[i] = EEPROM.read(EEPROM_CALIB_ADDR + i);
    }
    uint32_t magic =  (uint32_t)buf[0]
                   | ((uint32_t)buf[1] << 8)
                   | ((uint32_t)buf[2] << 16)
                   | ((uint32_t)buf[3] << 24);
    if (magic != BNO085_CALIB_MAGIC) {
        // EEPROM vide (toutes les cases a 0xFF) ou jamais calibree -> identite
        bno_data.q_offset_w = 1.0f;
        bno_data.q_offset_x = 0.0f;
        bno_data.q_offset_y = 0.0f;
        bno_data.q_offset_z = 0.0f;
        return;
    }
    uint16_t expected = crc16_ccitt(buf, 20);
    uint16_t stored   = (uint16_t)buf[20] | ((uint16_t)buf[21] << 8);
    if (expected != stored) {
        Serial.println("{\"warn\":\"BNO085 EEPROM calibration CRC invalid - fallback identity\"}");
        bno_data.q_offset_w = 1.0f;
        bno_data.q_offset_x = 0.0f;
        bno_data.q_offset_y = 0.0f;
        bno_data.q_offset_z = 0.0f;
        return;
    }
    memcpy(&bno_data.q_offset_w, buf + 4,  4);
    memcpy(&bno_data.q_offset_x, buf + 8,  4);
    memcpy(&bno_data.q_offset_y, buf + 12, 4);
    memcpy(&bno_data.q_offset_z, buf + 16, 4);
    Serial.print("{\"info\":\"EEPROM_LOADED q_offset=[\"");
    Serial.print(bno_data.q_offset_w, 4); Serial.print(",");
    Serial.print(bno_data.q_offset_x, 4); Serial.print(",");
    Serial.print(bno_data.q_offset_y, 4); Serial.print(",");
    Serial.print(bno_data.q_offset_z, 4); Serial.println("]\"}");
}

// ============================================================
// FIX NATIF v3 : calibration IMU = software-only, pas de reset hardware
// ============================================================
// fix v3 a supprime l'appel `bno.begin()` dans resetBNO085(). Chaque appel
// re-initialisait la session SHTP du BNO085, ce qui pouvait faire taire le
// chip (events jamais emis) et bloquer readBNO085() dans un getSensorEvent()
// infini. Maintenant la calibration est strictement logicielle : on arme
// juste `flag_capture_initial_pose=true`, et la PROCHAINE trame
// SENSOR_REPORTID_ROTATION_VECTOR dans readBNO085() devient la nouvelle
// pose de reference pour q_offset.
// ============================================================
void resetBNO085() {
    flag_capture_initial_pose = true;
    Serial.println("{\"info\":\"RESET_IMU_CMD_RECEIVED\"}");
    Serial.println("{\"info\":\"BNO085 calibration-pending (soft capture on next Rotation Vector)\"}");
}

// Factory-reset : efface la calibration en EEPROM + force q_offset a identite
// en RAM, sans toucher au hardware. Sert pour tests / debug.
void clear_calibration_from_eeprom() {
    bno_data.q_offset_w = 1.0f;
    bno_data.q_offset_x = 0.0f;
    bno_data.q_offset_y = 0.0f;
    bno_data.q_offset_z = 0.0f;
    save_calibration_init();
    Serial.println("{\"info\":\"BNO085 EEPROM calibration cleared (factory reset)\"}");
}

void setStand() {
    servos_enabled = true;
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (!servos[i].attached()) servos[i].attach(SERVO_PINS[i]);
        servo_targets[i] = SERVO_STAND[i];
    }
    Serial.println("{\"info\":\"stand\"}");
}
void setSit() {
    servos_enabled = true;
    for (int i = 0; i < NUM_SERVOS; i++) {
        if (!servos[i].attached()) servos[i].attach(SERVO_PINS[i]);
        servo_targets[i] = SERVO_SIT[i];
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
            servos[i].write((int)servo_current[i]);
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
// Publication JSON (BNO085 + Sonar)
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
    Serial.print("},\"version\":\"");
    Serial.print(SKETCH_VERSION);
    Serial.println("\"}");
}
