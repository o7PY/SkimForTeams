import type { ConfigContext, ExpoConfig } from 'expo/config';

// google-services.json is never committed (PHASE4.md section 4): EAS supplies it as a file env var, locally it sits next to this file.
const googleServicesFile = process.env.GOOGLE_SERVICES_JSON ?? './google-services.json';

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: 'Skim',
  slug: 'skim',
  version: '1.0.0',
  orientation: 'portrait',
  icon: './assets/images/icon.png',
  scheme: 'skim',
  // Over-the-air updates (P5-D-111): JS-only changes ship with `eas update`; the runtime version is the app version, so an update only
  // reaches builds of the same 1.0.x line. Anything native needs a new build (and a version bump).
  runtimeVersion: { policy: 'appVersion' },
  updates: { url: 'https://u.expo.dev/1990bc86-56ab-4f7a-a395-9c63b2e28fe0' },
  userInterfaceStyle: 'automatic', // light and dark both ship (P5-D-104); the app follows the phone's setting
  ios: { icon: './assets/expo.icon' }, // kept iOS-safe, never built in Phase 4 (P4-D-101)
  android: {
    package: 'in.skimforteams.app',
    adaptiveIcon: {
      backgroundColor: '#0F172A',
      foregroundImage: './assets/images/android-icon-foreground.png',
      backgroundImage: './assets/images/android-icon-background.png',
      monochromeImage: './assets/images/android-icon-monochrome.png',
    },
    predictiveBackGestureEnabled: false,
    permissions: ['android.permission.SCHEDULE_EXACT_ALARM'], // reminders on time on Android 12+ (the student grants it in Settings)
    googleServicesFile,
  },
  web: { output: 'static', favicon: './assets/images/favicon.png' },
  plugins: [
    'expo-router',
    ['expo-splash-screen', { backgroundColor: '#0F172A', image: './assets/images/splash-icon.png', imageWidth: 220 }],
    '@react-native-google-signin/google-signin', // Firebase flow: no options, the file above is used (library docs)
    'expo-secure-store',
    'expo-background-task',
    ['expo-calendar', { writeOnlyAccess: true }], // the system "new event" screen (P4-D-130); Android adds its calendar permissions
    ['expo-notifications', { icon: './assets/images/notification-icon.png', color: '#38BDF8', defaultChannel: 'changes' }],
  ],
  experiments: { typedRoutes: true, reactCompiler: true },
  extra: { eas: { projectId: '1990bc86-56ab-4f7a-a395-9c63b2e28fe0' } },
  owner: 'skimforteams',
});
