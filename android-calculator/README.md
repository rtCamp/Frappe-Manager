# Android Calculator App

A modern, feature-rich calculator application for Android built with Kotlin and Material Design 3.

## Features

- **Basic Operations**: Addition, Subtraction, Multiplication, Division
- **Decimal Support**: Perform calculations with decimal numbers
- **Percentage Calculations**: Quick percentage calculations
- **Delete Function**: Remove last digit entered
- **Clear Function**: Reset calculator to start new calculation
- **Modern UI**: Beautiful Material Design 3 interface with dark theme
- **Error Handling**: Handles division by zero and other edge cases

## Screenshots

The calculator features a clean, intuitive interface with:
- Large display area for viewing numbers and results
- Color-coded buttons (numbers, operators, and special functions)
- Rounded corners and modern styling
- Portrait orientation for optimal mobile use

## Requirements

- Android Studio Arctic Fox or later
- Minimum SDK: API 24 (Android 7.0)
- Target SDK: API 34 (Android 14)
- Kotlin 1.9.0+

## Installation

1. Clone this repository or download the `android-calculator` folder
2. Open Android Studio
3. Select "Open an Existing Project"
4. Navigate to the `android-calculator` folder and select it
5. Wait for Gradle to sync
6. Click "Run" or press Shift+F10

## Project Structure

```
android-calculator/
├── app/
│   ├── src/
│   │   └── main/
│   │       ├── java/com/calculator/app/
│   │       │   └── MainActivity.kt          # Main calculator logic
│   │       ├── res/
│   │       │   ├── layout/
│   │       │   │   └── activity_main.xml    # UI layout
│   │       │   ├── values/
│   │       │   │   ├── colors.xml          # Color definitions
│   │       │   │   ├── strings.xml         # String resources
│   │       │   │   └── themes.xml          # App theme
│   │       │   └── values-night/
│   │       │       └── themes.xml          # Dark theme
│   │       └── AndroidManifest.xml
│   └── build.gradle                        # App-level build config
├── build.gradle                            # Project-level build config
├── settings.gradle                         # Gradle settings
└── gradle.properties                       # Gradle properties
```

## How to Use

1. **Numbers**: Tap number buttons (0-9) to enter numbers
2. **Operations**: Tap +, -, ×, or ÷ to select operation
3. **Equals**: Tap = to calculate result
4. **Decimal**: Tap . to add decimal point
5. **Percent**: Tap % to calculate percentage
6. **Delete**: Tap DEL to remove last digit
7. **Clear**: Tap AC to clear everything and start fresh

## Technical Details

### Technologies Used
- **Language**: Kotlin
- **UI Framework**: Android Views with Material Components
- **Architecture**: Single Activity with View Binding
- **Minimum Android Version**: Android 7.0 (API 24)
- **Target Android Version**: Android 14 (API 34)

### Key Components
- `MainActivity.kt`: Contains all calculator logic and button handling
- `activity_main.xml`: Defines the UI layout with ConstraintLayout
- Material Design 3 components for modern, consistent UI

## Building the APK

To build a release APK:

1. In Android Studio, go to Build > Build Bundle(s) / APK(s) > Build APK(s)
2. Wait for the build to complete
3. Click "locate" in the notification to find your APK
4. The APK will be in `app/build/outputs/apk/debug/` or `app/build/outputs/apk/release/`

## Note on Launcher Icons

Before building for production, you should generate proper launcher icons:

1. In Android Studio, right-click on `res` folder
2. Select New > Image Asset
3. Configure your icon and let Android Studio generate all sizes
4. This will create the required mipmap resources for all screen densities

## Future Enhancements

Potential features to add:
- Scientific calculator mode
- Calculation history
- Landscape orientation support
- More advanced operations (square root, power, etc.)
- Theme customization options
- Haptic feedback

## License

This project is open source and available for educational purposes.

## Contributing

Feel free to fork this project and submit pull requests for any improvements.
