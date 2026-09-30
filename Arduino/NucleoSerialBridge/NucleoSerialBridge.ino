// USB-to-serial bridge for programming an ESP32-CAM through an STM32 Nucleo F446RE.
// Relays everything between the ST-LINK USB serial port and the ESP32-CAM.
// Wiring: ESP32 U0T -> D2, ESP32 U0R -> D8. Requires the STM32 Arduino core 3.x.

Uart EspSerial(PA10, PA9);  // RX = D2, TX = D8

void setup() {
  Serial.begin(115200);     // PC side (ST-LINK)
  EspSerial.begin(115200);  // ESP32 side
}

void loop() {
  while (Serial.available())    EspSerial.write(Serial.read());
  while (EspSerial.available()) Serial.write(EspSerial.read());
}
