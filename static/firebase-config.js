/**
 * firebase-config.js — Firebase web SDK configuration for Sightline.
 *
 * Copy this file to firebase-config.js and fill in your Firebase project's
 * web app credentials. These values are PUBLIC (used by the client-side
 * Firebase JS SDK) — they are NOT secret. Firebase security is enforced
 * server-side via the Admin SDK (firebase-service-account.json) and
 * Firestore Security Rules, not by hiding these values.
 *
 * Get these from: Firebase Console → Project Settings → General → Your apps → Web app.
 * https://console.firebase.google.com/
 *
 * If firebase-config.js is absent, Sightline runs in DESKTOP_MODE
 * (no Firebase auth, local admin bypass).
 */

window.FIREBASE_CONFIG = {
  apiKey: "AIzaSyBvQ0DAm4u5A6xjB1v9C5RRiJNaq9sHx2A",
  authDomain: "sightlinehumanitarian.firebaseapp.com",
  projectId: "sightlinehumanitarian",
  storageBucket: "sightlinehumanitarian.firebasestorage.app",
  messagingSenderId: "584583093173",
  appId: "1:584583093173:web:2bf3bade998cf149af57fc",
  measurementId: "G-SJMDY64ZKG"
};