#include "stm32f10x_rtc.h"
#include "stm32f10x_system.h"

int main(void) {
    // Enable the PWR and BKP clocks
    RCC_APB1ENR |= (RCC_APB1ENR_PWR | RCC_APB1ENR_BKP);

    // Wait for the LSE clock to be stable
    while (!(RCC_GetFlagStatus(RCC_LSERDY))) {}

    // Initialize the RTC
    RTC_InitTypeDef RTC_InitStruct;
    RTC_InitStruct.Asynchronous = FALSE;
    RTC_InitStruct.ClockSource = RTC_CLOCKSOURCE_LSE;
    RTC_Init(&RTC_InitStruct);

    // Set the RTC to use the LSE clock as its source
    RTC_SetClockSource(RTC_CLOCKSOURCE_LSE);

    // Set the RTC to generate an interrupt every second
    RTC_SetAlarm(RTC_ALARM_A, 1000); // 1 second

    // Enable the RTC alarm interrupt
    NVIC_EnableIRQ(RTC_IRQn);
    NVIC_SetPriority(RTC_IRQn, 5);

    while (1) {
        // Wait for the RTC alarm interrupt to occur
        __WFI();
    }
}

void RTC_IRQHandler(void) {
    // Handle the RTC alarm interrupt here
}