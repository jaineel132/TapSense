# Product Requirements Document (PRD)
# TapSense

**Document status:** Initial project definition  
**Scope:** Local development and local use only  
**Project type:** Windows desktop + local backend system  
**Reference:** Holo (conceptual/architectural reference only; no code reuse)

---

## 1. Product Overview

### 1.1 Product name

**TapSense**

### 1.2 One-line description

TapSense is a Windows-first desk interaction system that uses a laptop microphone to detect desk taps, infer which configured area of the desk was tapped, and trigger a user-defined computer action.

### 1.3 Core idea

A user places a Windows laptop on a stable desk and defines four interaction zones around it:

```text
                    DISPLAY SIDE

              LEFT REAR     RIGHT REAR
                    ┌──────────────┐
                    │              │
                    │    LAPTOP    │
                    │              │
                    └──────────────┘
              LEFT FRONT    RIGHT FRONT

                    TRACKPAD SIDE
```

The user taps one of these zones. TapSense captures the microphone signal, determines whether the sound is likely to be a tap, extracts useful signal features, classifies the tap into a zone, decides whether the classification is trustworthy, and executes the action assigned to that zone.

Example:

```text
User taps RIGHT FRONT
        ↓
Microphone captures sound
        ↓
Tap detector identifies an impact
        ↓
DSP extracts features
        ↓
Classifier predicts RIGHT_FRONT
        ↓
Confidence/rejection checks pass
        ↓
Action engine executes "Open VS Code"
```

---

## 2. Product Vision

Build a technically credible, privacy-friendly local interaction system that demonstrates how a real-world physical signal can be transformed into software behavior.

The project is intentionally designed as a major engineering project for learning and demonstrating:

- audio acquisition
- digital signal processing
- feature engineering
- classification
- calibration
- desktop application architecture
- REST API design
- PostgreSQL data modeling
- Redis usage
- authentication and authorization
- testing
- Git/GitHub engineering workflow
- system design

The project should be understandable end-to-end by the developer. AI coding tools may be used to generate implementation code, but the developer must understand the purpose, behavior, trade-offs, interfaces, failure modes, and architecture of every major component.

---

# 3. Problem Statement

Laptop users already have many ways to trigger actions: keyboard shortcuts, mouse actions, touchpads, voice assistants, and external hardware.

TapSense explores another interaction method: **using the physical desk surface around the laptop as a small programmable interaction area**.

The problem is not simply detecting sound. The software must distinguish an intentional desk tap from ordinary environmental noise and infer the approximate physical zone from the acoustic characteristics captured by the laptop microphone.

This creates several engineering challenges:

1. Continuous audio must be captured efficiently.
2. Candidate impacts must be detected without reacting to every sound.
3. Useful features must be extracted from short audio windows.
4. The system must classify taps into physical zones.
5. The classifier must be able to reject weak, ambiguous, or unfamiliar inputs.
6. The user must calibrate the system because desks, laptops, positions, microphones, and rooms differ.
7. Actions must execute safely and predictably.
8. The configuration and historical evaluation data should be accessible through a local backend.

---

# 4. Goals

## 4.1 Primary goals

TapSense must:

- run locally on a Windows laptop
- use the laptop microphone as the initial sensing device
- detect candidate desk taps
- distinguish likely taps from non-tap audio
- classify accepted taps into four configured zones
- provide a calibration workflow
- provide confidence/rejection behavior
- let users assign actions to zones
- execute actions locally on Windows
- maintain local profiles and configuration
- expose a local REST API
- persist structured application data in PostgreSQL
- use Redis only where it provides a real architectural benefit
- provide automated tests for major components
- remain explainable enough for technical interviews and project reviews

## 4.2 Secondary goals

- compare multiple signal-processing/classification approaches
- measure accuracy and latency rather than claiming accuracy without evidence
- maintain clear boundaries between audio processing, classification, actions, UI, and backend concerns
- keep the system privacy-conscious by processing audio locally by default
- create a repository that demonstrates professional engineering practices

---

# 5. Non-Goals / What TapSense Is NOT

TapSense is explicitly **not** intended to be:

- a general-purpose smart-home system
- a voice assistant
- a cloud audio-analysis platform
- a replacement for a physical keyboard or mouse
- a security/authentication system based on tap patterns
- a medical or safety-critical system
- a universal guarantee for every laptop, desk, room, or microphone setup
- an exact physical-coordinate tracker
- a copied implementation of Holo
- a multi-platform product in version 1
- a deployment/cloud project during the initial build

The initial project is **local-only**. Production deployment, cloud hosting, Kubernetes, distributed deployment, and public infrastructure are outside this PRD.

---

# 6. Target User

### Primary user

A technically curious Windows laptop user who wants programmable physical desk interactions and is willing to calibrate the system for a particular desk and laptop setup.

### Development/learning user

The project developer, who needs to understand the complete system deeply enough to explain:

- why each subsystem exists
- how data flows through it
- what assumptions it makes
- how it fails
- how it is tested
- what trade-offs were made

---

# 7. Core User Experience

## 7.1 First use

```text
Launch TapSense
      ↓
Create/select local device profile
      ↓
Configure desk/laptop setup
      ↓
Start calibration
      ↓
Tap each zone repeatedly
      ↓
System validates samples
      ↓
Train/build classifier profile
      ↓
Run held-out evaluation
      ↓
Save profile if quality is acceptable
      ↓
Assign actions
      ↓
Enter Listening/Desk mode
```

## 7.2 Normal use

```text
Microphone continuously listens
          ↓
Candidate audio event appears
          ↓
Tap detector evaluates event
          ↓
If rejected → ignore
          ↓
If accepted → extract features
          ↓
Classifier predicts a zone
          ↓
Rejection/confidence checks
          ↓
If uncertain → ignore/report rejection
          ↓
If accepted → action dispatcher
          ↓
Windows executes configured action
```

## 7.3 Recalibration

A user can create or replace a profile when the laptop position, desk, or environment changes enough to affect classification quality.

---

# 8. High-Level System Architecture

```text
                         TAPSense SYSTEM

┌─────────────────────────────────────────────────────────────┐
│                     Windows Desktop App                    │
│                                                             │
│  ┌────────────┐    ┌─────────────┐    ┌─────────────────┐  │
│  │ Audio      │ -> │ Tap / DSP   │ -> │ Classifier      │  │
│  │ Capture    │    │ Pipeline    │    │ + Rejection     │  │
│  └────────────┘    └─────────────┘    └───────┬─────────┘  │
│                                               │            │
│                                               v            │
│                                      ┌─────────────────┐   │
│                                      │ Action Engine   │   │
│                                      └─────────────────┘   │
│                                                             │
│                    Desktop UI / State                      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               │ Local HTTP
                               v
                    ┌──────────────────────┐
                    │     Local Backend    │
                    │ Node.js + TypeScript │
                    │       Express        │
                    └──────────┬───────────┘
                               │
                 ┌─────────────┴─────────────┐
                 v                           v
          ┌──────────────┐            ┌──────────────┐
          │ PostgreSQL   │            │    Redis     │
          │ persistent   │            │ cache/state  │
          │ application  │            │ where useful │
          │ data         │            └──────────────┘
          └──────────────┘
```

### Architectural principle

The desktop application owns **real-time local sensing and action execution**.

The backend owns **application configuration, identities, profiles, historical records, and API-level state**.

The backend must not become a real-time dependency for every individual tap. Tap recognition and action triggering should continue to function locally even when the local API is unavailable, subject to the features being used.

---

# 9. Major Components

## 9.1 Audio Capture Layer

Responsibilities:

- open/select the microphone input
- stream audio continuously
- produce predictable audio buffers
- handle start/stop lifecycle
- detect/handle capture failures
- expose a controlled stream to the DSP pipeline

Does NOT decide which zone was tapped.

## 9.2 Tap Detection Layer

Responsibilities:

- estimate background/noise conditions
- detect candidate impact/onset events
- isolate candidate time windows
- suppress obvious non-tap events
- enforce refractory/cooldown behavior

Output:

```text
CandidateTap {
    timestamp
    audio_window
    detection_metrics
}
```

## 9.3 DSP / Feature Extraction Layer

Responsibilities:

- convert the short audio window into useful numerical features
- calculate time-domain features
- calculate frequency-domain features
- normalize features consistently
- expose deterministic feature vectors for training and inference

Example abstraction:

```text
Audio Window
    ↓
Pre-processing
    ↓
FFT / spectral analysis
    ↓
Feature calculations
    ↓
Feature normalization
    ↓
Feature Vector
```

## 9.4 Classification Layer

Responsibilities:

- train a zone model from labeled calibration examples
- predict one of the four zones
- calculate confidence or decision strength
- reject uncertain/unfamiliar input
- expose model/version information

Initial zone classes:

- LEFT_FRONT
- LEFT_REAR
- RIGHT_FRONT
- RIGHT_REAR

Optional output:

- UNKNOWN / REJECTED

## 9.5 Calibration Layer

Responsibilities:

- guide the user through zone collection
- collect labeled examples
- validate sample quality
- detect inconsistent calibration data
- train/update a local model
- run held-out evaluation
- save a profile version

## 9.6 Action Engine

Responsibilities:

- map a zone to a configured action
- validate action configuration
- execute supported actions safely
- report success/failure
- prevent actions from being triggered by rejected events

Initial action categories may include:

- open application
- open URL
- run a local command/script
- copy configured text
- media control
- screenshot

Exact action support will be finalized during implementation based on Windows capabilities and safe execution constraints.

## 9.7 Desktop UI Layer

Responsibilities:

- show current listening state
- visualize four zones
- guide calibration
- configure actions
- show evaluation results
- show diagnostics/errors
- manage local profiles/settings

## 9.8 Backend API

Responsibilities:

- local authentication/identity handling if enabled for the local product model
- device/profile configuration
- action configuration
- calibration/evaluation metadata
- historical records
- API validation
- consistent errors
- access control

## 9.9 PostgreSQL

Stores durable structured application data such as:

- users (if user accounts are enabled)
- devices
- profiles
- zones
- actions
- calibration sessions
- calibration metadata
- evaluations
- tap-event metadata/analytics where useful

Raw audio should not be stored in PostgreSQL by default.

## 9.10 Redis

Redis is optional until a concrete use case exists.

Potential uses:

- temporary calibration state
- cached frequently-read profiles/configuration
- rate limiting
- short-lived state

Redis must not be included purely for resume value; each use must be justified architecturally.

---

# 10. Technology Stack

## Desktop / signal-processing side

- **Python** — primary implementation language for the first Windows prototype
- **NumPy** — numerical arrays and signal calculations
- **SciPy** — scientific/signal-processing functionality
- **scikit-learn** — initial classical machine-learning models
- **sounddevice** or equivalent Windows-compatible audio input library — microphone streaming
- **PySide6** — desktop UI
- **pytest** — Python tests

Library choices may be adjusted after validating Windows compatibility and actual project needs.

## Backend

- **Node.js**
- **TypeScript**
- **Express.js**
- **Prisma** as the initial ORM/data-access layer
- **PostgreSQL**
- **Redis** where justified
- **Zod or equivalent** for request validation
- **Jest + Supertest** or equivalent Node testing stack
- **OpenAPI/Swagger** for API documentation

## Development tooling

- Git
- GitHub
- VS Code
- local PostgreSQL
- local Redis
- optional Docker for local environment consistency later in the project

All planned software dependencies are intended to be usable without paid subscriptions. The initial system does not require paid APIs, cloud databases, commercial SDKs, or paid hosting.

---

# 11. Data Flow

## 11.1 Real-time tap path

```text
Microphone
   ↓
Audio buffer
   ↓
Noise / onset detection
   ↓
Candidate  tap
   ↓
Short analysis window
   ↓
Feature extraction
   ↓
Feature normalization
   ↓
Zone classifier
   ↓
Confidence + rejection gates
   ↓
Accepted zone
   ↓
Action lookup
   ↓
Action validation
   ↓
Windows action execution
```

## 11.2 Calibration path

```text
User selects profile
        ↓
Calibration starts
        ↓
Zone highlighted
        ↓
User taps
        ↓
Tap detection
        ↓
Feature extraction
        ↓
Sample quality validation
        ↓
Labeled sample stored
        ↓
Repeat for all zones
        ↓
Training
        ↓
Held-out evaluation
        ↓
Quality gate
        ↓
Profile/model saved
```

## 11.3 Configuration path

```text
Desktop UI
    ↓
Local API request
    ↓
Express route
    ↓
Validation
    ↓
Service layer
    ↓
Prisma/data access
    ↓
PostgreSQL
    ↓
Response
    ↓
Desktop state updated
```

---

# 12. Core Domain Model

Initial conceptual relationships:

```text
User
  │
  ├───────────────┐
  │               │
  v               v
Device          (optional local identity)
  │
  v
Profile
  │
  ├──────────┬─────────────┐
  v          v             v
Zones      Actions     Evaluations
  │
  v
Calibration Sessions
  │
  v
Calibration Samples
```

A simplified relational model:

```text
users
  |
  | 1:N
  v
 devices
  |
  | 1:N
  v
profiles
  |
  +---- 1:N ---- zones
  |
  +---- 1:N ---- actions
  |
  +---- 1:N ---- calibration_sessions
  |                    |
  |                    +---- 1:N ---- calibration_samples
  |
  +---- 1:N ---- evaluations
```

The exact schema will be designed in the PostgreSQL/data-modeling module rather than prematurely fixed in this PRD.

---

# 13. Project Modules / Development Roadmap

The project is divided into modules. Each module should produce a meaningful artifact or verified capability before moving forward.

---

## Module 0 — Problem Definition & Engineering Design

### Purpose

Understand the complete problem and define the architecture before coding.

### Subtopics

- problem statement
- user experience
- functional requirements
- non-functional requirements
- Holo reference analysis
- constraints
- architecture
- repository structure
- module boundaries
- success criteria

### Output

- requirements document
- initial architecture diagram
- repository structure plan
- glossary/domain terminology

### Must-understand questions

- What exactly is TapSense?
- Why does it need audio processing?
- Why is calibration necessary?
- Why does the system need a backend at all?
- What belongs on the client vs backend?

---

## Module 1 — Audio & Signal Fundamentals

### Subtopics

- sound as a signal
- waveform
- amplitude
- frequency
- sampling
- sample rate
- Nyquist concept
- audio buffers
- time domain
- frequency domain
- noise
- SNR
- desk resonance intuition

### Output

A small local experiment that records and visualizes audio.

---

## Module 2 — Windows Audio Capture Engine

### Subtopics

- microphone access
- audio stream lifecycle
- chunks/buffers
- sample formats
- channels
- device selection
- error handling
- start/stop
- resource cleanup

### Output

Reliable microphone streaming into the application.

---

## Module 3 — Tap Detection

### Subtopics

- amplitude/energy detection
- onset detection
- adaptive noise floor
- thresholds
- debounce/refractory period
- candidate windows
- speech rejection
- typing/environmental noise
- false positives
- false negatives
- latency

### Output

Reliable TAP / NOT-TAP detector with measurable behavior.

---

## Module 4 — DSP & Feature Extraction

### Subtopics

- preprocessing
- FFT
- spectrum
- spectral energy
- spectral centroid
- zero-crossing rate
- frequency bands
- MFCC/log-mel-style features
- temporal features
- normalization
- feature vectors
- feature visualization

### Output

Deterministic feature extraction pipeline.

---

## Module 5 — Zone Classification

### Subtopics

- supervised classification
- labels
- training data
- train/validation/test split
- nearest-neighbor baseline
- logistic regression
- random forest or similar baseline
- confidence
- rejection
- ambiguity
- out-of-distribution ideas
- confusion matrix
- accuracy
- precision/recall
- latency

### Output

Four-zone classifier that can accept or reject a candidate tap.

---

## Module 6 — Calibration System

### Subtopics

- guided calibration
- zone sequencing
- sample counts
- sample quality
- weak/noisy samples
- retries
- model training
- model versioning
- profile persistence
- held-out evaluation
- calibration quality gate
- recalibration

### Output

Reusable calibrated profile tied to a specific laptop/desk setup.

---

## Module 7 — Action Engine

### Subtopics

- action abstraction
- action registry
- configuration
- Windows process execution
- URL opening
- applications
- scripts
- media actions
- screenshots
- validation
- failure handling
- safe execution

### Output

Zone-to-action execution pipeline.

---

## Module 8 — Windows Desktop Application

### Subtopics

- desktop architecture
- UI/state separation
- dashboard
- zone visualization
- calibration UI
- action editor
- evaluation UI
- settings
- lifecycle
- background listening
- errors

### Output

Usable local Windows desktop application.

---

## Module 9 — Backend Foundation

### Subtopics

- client/server reasoning
- REST
- HTTP request/response lifecycle
- routing
- controllers
- services
- repositories/data access
- validation
- error handling
- environment configuration
- logging

### Output

Clean local Node.js/TypeScript/Express backend skeleton.

---

## Module 10 — PostgreSQL & Data Modeling

### Subtopics

- entities
- relationships
- primary keys
- foreign keys
- constraints
- normalization
- indexes
- transactions
- SQL
- migrations
- Prisma

### Output

Versioned local database schema and data-access layer.

---

## Module 11 — Authentication & Authorization

### Subtopics

- registration
- password hashing
- login
- access tokens
- refresh tokens
- middleware
- authorization
- ownership
- logout/revocation concepts
- security basics

### Output

Protected local API.

---

## Module 12 — Profiles & Configuration APIs

### Subtopics

- device registration
- profile CRUD
- zone configuration
- action configuration
- active profile
- versioning
- validation
- error contracts

### Output

Backend-managed configuration consumed by the desktop client.

---

## Module 13 — Calibration & Evaluation APIs

### Subtopics

- calibration sessions
- metadata
- sample metadata
- model versions
- evaluation results
- accuracy history
- latency history
- reporting

### Output

Historical evaluation and calibration APIs.

---

## Module 14 — Redis & Performance

### Subtopics

- caching rationale
- Redis data structures needed by the project
- cache-aside pattern
- TTL
- profile/config cache
- temporary state
- rate limiting
- invalidation
- Redis failure behavior

### Output

Measured, justified Redis integration.

---

## Module 15 — Client ↔ Backend Integration

### Subtopics

- API client
- authentication from client
- requests/responses
- timeouts
- retries
- offline behavior
- local fallback
- synchronization
- conflict handling

### Output

Integrated local desktop + backend system.

---

## Module 16 — Testing & Quality

### Subtopics

- unit testing
- integration testing
- API testing
- database tests
- audio pipeline tests
- classifier tests
- calibration tests
- action tests
- end-to-end scenarios
- mocking
- fixtures
- regression tests

### Output

Automated test suite covering critical behavior.

---

## Module 17 — Security & Reliability

### Subtopics

- input validation
- command execution risks
- authorization
- secrets
- SQL injection concepts
- rate limiting
- error leakage
- resource exhaustion
- crash recovery
- privacy
- failure modes

### Output

Security/reliability review and fixes.

---

## Module 18 — Final Local Integration

### Subtopics

- complete local startup flow
- client + backend + PostgreSQL + Redis
- configuration
- local environment setup
- data reset/recovery
- final integration tests
- performance measurement
- profiling

### Output

A complete reproducible local system.

**Important:** Deployment/cloud hosting is intentionally excluded from this PRD. Deployment concepts may be taught separately after the project is complete.

---

# 14. Local-Only Architecture Constraint

During the initial project, all core infrastructure runs locally.

```text
┌─────────────────────────────────────────────┐
│                YOUR WINDOWS PC              │
│                                             │
│  ┌─────────────┐     ┌──────────────────┐  │
│  │ TapSense UI │────▶│ TapSense Client  │  │
│  └─────────────┘     └─────────┬────────┘  │
│                                │            │
│                                │ localhost  │
│                                v            │
│                    ┌─────────────────────┐  │
│                    │ Local Node Backend  │  │
│                    └───────┬──────┬──────┘  │
│                            │      │          │
│                            v      v          │
│                      PostgreSQL  Redis       │
│                                             │
└─────────────────────────────────────────────┘
```

No cloud dependency is required for the initial product.

---

# 15. Initial API Surface (Conceptual)

The API will be finalized after the database/domain model is designed.

Possible endpoint groups:

```text
/api/auth
/api/devices
/api/profiles
/api/profiles/:profileId/zones
/api/profiles/:profileId/actions
/api/profiles/:profileId/calibration
/api/profiles/:profileId/evaluations
/api/events
```

Example operations:

```text
POST   /api/auth/register
POST   /api/auth/login

POST   /api/devices
GET    /api/devices

POST   /api/profiles
GET    /api/profiles
GET    /api/profiles/:id
PUT    /api/profiles/:id
DELETE /api/profiles/:id

GET    /api/profiles/:id/zones
PUT    /api/profiles/:id/zones/:zoneId

POST   /api/profiles/:id/calibration
POST   /api/profiles/:id/evaluations
GET    /api/profiles/:id/evaluations
```

These are design candidates, not final contracts.

---

# 16. Functional Requirements

## FR-01 Audio capture

The system shall capture microphone audio continuously during listening mode.

## FR-02 Tap detection

The system shall identify candidate tap events from the live audio stream.

## FR-03 Rejection of non-taps

The system shall reject obvious sustained/noisy events when they do not match the expected tap characteristics.

## FR-04 Feature extraction

The system shall convert accepted candidate windows into deterministic feature vectors.

## FR-05 Zone classification

The system shall classify a candidate into one of four configured zones or reject it.

## FR-06 Calibration

The system shall guide the user through a zone calibration process.

## FR-07 Evaluation

The system shall support held-out evaluation and report meaningful quality metrics.

## FR-08 Profiles

The system shall support saved local configuration/model profiles.

## FR-09 Action mapping

The system shall map each zone to a configured action.

## FR-10 Action execution

The system shall execute valid configured actions locally.

## FR-11 Local API

The system shall expose a local REST API for configuration and historical data.

## FR-12 Persistence

The system shall persist structured application data locally in PostgreSQL.

## FR-13 Optional caching/state

The system may use Redis for justified temporary state or caching.

## FR-14 Testing

Critical components shall have automated tests.

---

# 17. Non-Functional Requirements

## NFR-01 Local processing

Raw audio processing for tap detection and classification should occur locally by default.

## NFR-02 Privacy

Raw audio shall not leave the local machine as part of normal operation.

## NFR-03 Latency

The interaction should feel near-instantaneous. A concrete latency target will be established empirically during implementation and benchmarking.

## NFR-04 Resource usage

The application should avoid unnecessary CPU and memory consumption during idle listening.

## NFR-05 Reliability

The application should fail safely when the microphone becomes unavailable, the classifier rejects input, an action fails, or the backend is unavailable.

## NFR-06 Explainability

The system's major decisions and data flow must be explainable to a technically knowledgeable reviewer.

## NFR-07 Maintainability

The codebase should maintain clear module boundaries and automated verification.

---

# 18. Success Criteria

TapSense is considered a successful project when all of the following are true:

1. A Windows laptop can capture audio locally.
2. The system can detect intentionally produced taps with useful reliability.
3. The system can extract repeatable features from candidate taps.
4. A calibrated model can distinguish the four configured zones with measurable accuracy.
5. The system can reject at least some common non-tap/ambiguous inputs.
6. A user can configure actions for each zone.
7. Accepted taps trigger only their intended actions.
8. Calibration/evaluation data can be persisted and retrieved.
9. The desktop client can communicate with the local backend.
10. PostgreSQL stores structured persistent data.
11. Redis is either meaningfully used or intentionally omitted after evaluation.
12. Automated tests cover critical functionality.
13. The complete local system can be explained end-to-end by the developer.

Accuracy is not to be claimed without real measured evaluation on the tested laptop/desk setup.

---

# 19. Key Engineering Questions We Must Answer

Throughout the project, we should be able to answer these questions without relying on generated code or memorized definitions:

### Audio

- How does a microphone convert sound into samples?
- Why does sample rate matter?
- Why are audio buffers necessary?
- What does the raw waveform tell us?

### DSP

- Why do we use the FFT?
- What is the difference between time-domain and frequency-domain information?
- Why do different desk zones produce different feature patterns?
- Why normalize features?

### Classification

- Why is this a supervised classification problem?
- Why start with a simple classifier?
- What is confidence?
- What does rejection mean?
- Why can high calibration accuracy still produce poor real-world performance?

### Calibration

- Why is one model/profile not necessarily valid on every desk?
- Why do we need held-out evaluation?
- What makes a training sample weak or noisy?

### Actions

- How do we safely execute local actions?
- How do we prevent rejected taps from triggering actions?
- What happens when an action fails?

### Backend

- Why does the client need an API?
- Which state belongs in PostgreSQL?
- What should never be stored in the database?
- Why would Redis help here?
- What should happen when the backend is unavailable?

### Architecture

- Why is the real-time tap loop local?
- Why should the backend not sit in the critical path for every tap?
- What are the boundaries between client, backend, and database?

---

# 20. Development Philosophy

TapSense will be built using the following rules:

### Rule 1 — Understand before implementation

Before coding a major subsystem, first understand the problem, inputs, outputs, architecture, and trade-offs.

### Rule 2 — AI may write code; the developer owns understanding

AI coding tools may be used to generate boilerplate, implementations, tests, refactors, and documentation. Generated code must be reviewed and understood before being accepted.

### Rule 3 — Build incrementally

Do not build the whole system in one pass. Each module should leave a working artifact.

### Rule 4 — Measure instead of assuming

Audio classification quality, latency, false positives, and resource usage must be measured experimentally.

### Rule 5 — Keep the architecture honest

Do not add technologies simply to make the project look advanced. Every component must solve an actual problem.

### Rule 6 — Prefer simple baselines first

Start with understandable approaches and introduce complexity only when evidence shows that it is needed.

### Rule 7 — Document decisions

Important architecture and implementation decisions should explain what was chosen, why it was chosen, and what alternatives were rejected.

---

# 21. Reference to Holo

Holo is used only as a **conceptual/reference project**.

We may study its:

- problem framing
- calibration concepts
- signal-processing ideas
- four-zone interaction model
- rejection/evaluation concepts
- architecture/documentation practices

We will independently implement TapSense for Windows and will not copy source code, proprietary assets, or undocumented implementation details.

---

# 22. Project Boundaries for Version 1

Version 1 will focus on:

```text
Windows laptop
      +
laptop microphone
      +
four desk zones
      +
local signal processing
      +
local classifier
      +
local actions
      +
local desktop UI
      +
local REST backend
      +
local PostgreSQL
      +
optional local Redis
```

Version 1 will not focus on:

```text
Cloud hosting
Public deployment
Kubernetes
Multi-region systems
Mobile apps
macOS implementation
Linux implementation
Specialized hardware
Large neural networks
Commercial APIs
```

---

# 23. Final System Picture

```text
                     TAPSense

       ┌────────────────────────────────┐
       │         Physical World         │
       │                                │
       │   User taps one desk zone      │
       └────────────────┬───────────────┘
                        │
                        v
       ┌────────────────────────────────┐
       │        Windows Laptop          │
       │                                │
       │  Microphone                    │
       │      ↓                         │
       │  Tap Detection                 │
       │      ↓                         │
       │  DSP / Feature Extraction      │
       │      ↓                         │
       │  Zone Classifier               │
       │      ↓                         │
       │  Confidence / Rejection        │
       │      ↓                         │
       │  Action Engine                 │
       └────────────────┬───────────────┘
                        │
                        │ local HTTP
                        v
       ┌────────────────────────────────┐
       │       Local Backend API        │
       │                                │
       │ Node.js + TypeScript + Express │
       └───────────────┬────────────────┘
                       │
               ┌───────┴────────┐
               v                v
       ┌──────────────┐  ┌──────────────┐
       │ PostgreSQL   │  │    Redis     │
       │ durable data │  │ cache/state  │
       └──────────────┘  └──────────────┘
```

---

# 24. Immediate Next Step

The project will begin with **Module 0**.

The first learning topic is:

> **Module 0.1 — What problem are we actually solving, and why is it technically difficult?**

Before implementation, we will establish:

- the exact user problem
- the core requirements
- the important assumptions
- the system boundaries
- the terminology used throughout the project
- the first architecture decisions

No deployment work is part of the current PRD.
