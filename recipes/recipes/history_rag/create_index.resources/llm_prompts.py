# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Defines prompt generation functions for the topic clustering pipeline.
This allows for consistent prompt generation across different scripts.
"""


def get_summary_and_title_prompt(messages: list[str]) -> str:
  """
  Generates the prompt for summarizing a topic and creating a title in a single call.
  The messages should be pre-sorted chronologically.
  """
  glossary = {
    'ANGLE': 'Almost Native Graphics Layer Engine. A graphics abstraction layer that translates OpenGL ES calls to other graphics APIs like DirectX, Vulkan, Metal, or desktop OpenGL.',
    'ARC++': 'Android Runtime for Chrome. Technology to run Android applications on ChromeOS.',
    'ASan': 'AddressSanitizer - A tool for detecting memory addressability issues.',
    'Ash': "Ash (Aura Shell): The window manager and system UI for ChromeOS, including the shelf, app launcher, system tray, and window controls. It's built using Aura and Views.",
    'Aura': 'The hardware-accelerated UI framework used by Chrome and ChromeOS for windowing and event handling. It underpins the Views toolkit on several platforms.',
    'AX': 'Accessibility. Prefixes many accessibility-related classes.',
    'BeginFrame': "A signal from the display compositor (Viz) to clients (like Blink or the UI compositor) indicating when it's appropriate to start producing a new frame, typically synchronized with the display refresh rate.",
    'Blink': 'The rendering engine used in Chromium to turn HTML, CSS, and JavaScript into pixels on the screen. It runs within the Renderer Process.',
    'Browser Process': "The main process in Chrome's multi-process architecture. It's responsible for the main UI (windows, tabs, toolbar), managing renderer processes, network requests, and access to system resources.",
    'Builtins': 'Functions implemented within V8 itself, often in Torque or CSA, providing core JavaScript functionality (e.g., Array.prototype.map, operator implementations).',
    'CAP': 'Composite After Paint. A project to re-implement the Blink/cc interface, where Blink produces property trees and a display list, and CC/Viz handles compositing. Previously known as Slimming Paint v2 (SPv2).',
    'CAS': 'Content Addressed Storage - Service for storing and transferring binary blobs, used in the build process.',
    'CC': 'Historically "Chrome Compositor". It\'s responsible for taking painted inputs (often from Blink or the UI Compositor), managing layer trees, coordinating rasterization of content into GPU textures, and producing CompositorFrames for the Display Compositor (Viz). Located in //cc.',
    'CI': 'Continuous Integration - The automated process of building and testing changes.',
    'CIPD': 'Chrome Infra Package Deployer - A LUCI service for hosting and distributing packages (SDKs, toolchains, etc.).',
    'CL': 'Change List - A set of changes to the codebase, typically corresponding to a single Git commit.',
    'Clank': 'The codename for Chrome on Android.',
    'CodeStubAssembler (CSA)': 'An abstraction layer within V8 for generating low-level machine code in a platform-independent way. Torque compiles down to CSA C++ code.',
    'CommandBuffer': 'An abstraction for a stream of GPU commands, used for communicating with the GPU process.',
    'CompositorFrame': 'A collection of quads and resources (textures) organized into RenderPasses, representing the work a client wants the Display Compositor (Viz) to draw.',
    'CompositorFrameSink': 'An interface (often over Mojo IPC) used by clients (Blink, UI Compositor) to submit CompositorFrames to Viz.',
    'Content Module': "A major layer in the Chromium architecture that encapsulates the multi-process rendering of web pages. Its primary class is WebContents. It's designed to be embeddable in different browser shells.",
    'CQ': 'Commit Queue - The automated system that tests a CL before it is merged into the codebase.',
    'CRTC': 'CRT Controller. Part of the display hardware pipeline.',
    'CRX': 'Chrome Extension - The file format for packaging Chrome extensions and apps.',
    'CSS': 'Cascading Style Sheets.',
    'CV': 'Change Verifier - A LUCI service that manages the CQ process.',
    'd8': 'The V8 developer shell, a command-line tool used for running JavaScript files, testing, debugging, and benchmarking V8.',
    'DComp': 'DirectComposition. A Windows API for hardware-accelerated compositing.',
    'Deoptimization': 'The process of reverting from optimized machine code (produced by TurboFan, Maglev, or Sparkplug) back to Ignition bytecode. This happens if assumptions made during optimization turn out to be false at runtime (e.g., object types change).',
    'DIP': 'Device Independent Pixel. An abstraction over physical pixels to handle different screen densities.',
    'Display Compositor': "The component within Viz (specifically //components/viz/service/display) that receives CompositorFrames from multiple clients, aggregates them, and draws the final scene to the screen, often using GPU acceleration.",
    'DisplayList': "A recorded sequence of drawing commands (e.g., PaintOps) produced by Blink's paint stage, representing the visual content of a layer.",
    'DMABuf': 'DMA Buffer Sharing. A Linux kernel mechanism for zero-copy buffer sharing between the CPU, GPU, and other peripherals, commonly used in ChromeOS graphics.',
    'DOM': 'Document Object Model.',
    'DPU': 'Display Processing Unit. Specialized hardware for display tasks.',
    'DRM': 'Direct Rendering Manager. The Linux kernel subsystem for interfacing with modern GPU hardware, used extensively on ChromeOS and Linux.',
    'DUT': 'Device Under Test - The device (e.g., Chromebook, phone, desktop) on which Chromium or ChromeOS is being tested.',
    'EGL': 'Native Platform Graphics Interface. An interface between Khronos rendering APIs (like OpenGL ES) and the underlying native platform windowing system.',
    'Eternal': 'Persistent handles that are never garbage collected, used for constants.',
    'External Pointers': 'Pointers from the V8 heap to memory outside the V8 sandbox (e.g., embedder objects, native code). These are often managed through a table to enhance security.',
    'GC': 'Garbage Collection. The process of reclaiming memory occupied by objects that are no longer in use. V8 uses a generational, mostly concurrent, and parallel garbage collector. Key phases include Mark-Sweep-Compact.',
    'GBM': 'Generic Buffer Management. A Linux API, often used with DRM, for managing graphics buffers.',
    'GL': 'OpenGL. A cross-platform graphics API.',
    'GLES': 'OpenGL for Embedded Systems. A subset of OpenGL suitable for mobile and embedded devices.',
    'GLic': 'Gemini Live In Chrome. An API for AI features including fetching browser data, managing panel state, and handling user profile information.',
    'GPU': 'Graphics Processing Unit.',
    'GPU Process': 'A sandboxed Chromium process responsible for handling GPU operations, including 3D rendering (WebGL, WebGPU), video decoding/encoding, and display compositing (Viz).',
    'GpuMemoryBuffer': 'A handle to a block of memory accessible by the GPU, potentially sharable between processes.',
    'GMB': 'GpuMemoryBuffer. A handle to a block of memory accessible by the GPU, potentially sharable between processes.',
    'Handles (Local, Global, Persistent, Eternal)': 'Smart pointers used in the V8 C++ API to manage references to JavaScript objects on the V8 heap. They are essential for preventing objects from being garbage collected while C++ code still needs them.',
    'Heap': 'The memory area managed by V8 where JavaScript objects are allocated. V8 has a generational garbage collector.',
    'Hidden Classes / Maps / Shapes': "Internal V8 objects that describe the structure of a JavaScript object, including its properties and layout. Objects with the same Map can often be optimized together.",
    'HWND': 'Handle to a Window. A Windows-specific opaque handle for a native window.',
    'IDL': 'Interface Definition Language. Used for defining interfaces, for example, in Mojo for IPC.',
    'Ignition': "V8's bytecode interpreter. JavaScript source code is first compiled into Ignition bytecode, which can be executed quickly. Ignition also collects profiling information.",
    'Inline Caches (ICs)': "A key optimization technique. ICs cache the results of property lookups or operations based on the object's shape/type. This makes repeated property accesses much faster.",
    'IPC': 'Inter-Process Communication - The mechanisms by which different Chrome processes (e.g., browser, renderer, GPU) communicate.',
    'Isolate': 'An isolated instance of the V8 engine. Each Isolate has its own heap and garbage collector. Multiple Isolates can run in parallel (typically on different threads), but objects cannot be directly shared between them. This is the primary sandbox for V8 execution.',
    'LaCrOS': 'Linux and ChromeOS. A project to separate the Chrome browser from the ChromeOS system UI (Ash), making browser updates independent of OS updates.',
    'Layer': 'Represents a textured quad or a set of drawing commands that can be composited together by the GPU. Views can be backed by layers (View::SetPaintToLayer()) to enable effects like transparency and transforms.',
    'LayerTreeHost': 'The main interface for Blink or the UI Compositor to interact with CC, managing the layer tree.',
    'Layout': 'The process in Blink of determining the size and position of elements in the DOM.',
    'LGTM': 'Looks Good To Me - Used in code reviews to indicate approval of a CL.',
    'LKGR': 'Last Known Good Revision - The most recent revision of the code that passed all tests.',
    'Local': 'Scoped handles, valid only within the current HandleScope.',
    'LSan': 'LeakSanitizer - A tool for detecting memory leaks.',
    'Maglev': "Another optimizing compiler, sitting between Sparkplug and TurboFan. It's faster than TurboFan but produces less optimized code.",
    'Mojo': "Chromium's IPC framework for communication between processes.",
    'MSan': 'MemorySanitizer - A tool for detecting uses of uninitialized memory.',
    'NTP': 'New Tab Page - The page that appears when a new tab is opened.',
    'Omnibox': 'The combined address bar and search box at the top of the browser window.',
    'OOBE': 'Out-Of-Box Experience - The initial setup flow a user goes through when using a device for the first time.',
    'OOP-D': 'Out-of-Process Display Compositor. This refers to the Viz service running in the GPU process.',
    'OOP-R': 'Out-of-Process Rasterization. Moving the rasterization task from the Renderer process to the GPU process.',
    'OOPR': 'Out-of-Process Rasterization - Moving rasterization tasks to a dedicated GPU process.',
    'Paint': 'The process in Blink of converting the layout tree into drawing commands (DisplayLists).',
    'PaintOp': 'A single drawing operation (e.g., draw rect, draw text) recorded in a DisplayList.',
    'PaintRecord': 'Equivalent to cc::PaintOpBuffer, a sequence of PaintOps.',
    'Pointer Compression': 'On 64-bit systems, V8 can store heap pointers as 32-bit values relative to a base address, saving memory, particularly within the sandbox.',
    'Profile': "Represents a user's data, including history, bookmarks, settings, extensions, and cookies. Chrome supports multiple profiles. Internally, this often corresponds to the Profile class, which is a type of BrowserContext.",
    'Property Trees': 'Data structures in CC (and increasingly produced by Blink in CAP) that store hierarchical properties like transforms, clips, and effects, separate from the layer list. This allows for more efficient updates.',
    'PTAL': 'Please Take Another Look - Used in code reviews to request renewed attention from reviewers after addressing comments.',
    'Quad': 'A four-sided polygon, typically a rectangle, used within CompositorFrames to specify a piece of content to be drawn, with associated material (texture, color) and transformations.',
    'Raster': 'Raster(ization). The process of converting drawing commands (like DisplayLists or PaintOps) into pixels, typically stored in GPU textures or bitmaps. This can happen in the Renderer process or GPU process (OOP-R).',
    'RenderPass': 'A sequence of drawing commands (Quads) within a CompositorFrame that are executed together, often rendering to an intermediate texture to achieve effects like masking or blending.',
    'Renderer Process': 'A sandboxed process responsible for rendering web content. Each tab (typically) runs in its own renderer process, isolating it from the browser process and other tabs for security and stability. This process hosts the Blink rendering engine.',
    'Skia': 'The 2D graphics library used by Chromium for drawing text, shapes, and images.',
    'Snapshot': 'A serialized representation of the V8 heap, capturing the initial state of built-in objects and contexts. This allows V8 to start up faster by loading the snapshot instead of creating all objects from scratch.',
    'Sparkplug': 'A baseline compiler that sits between Ignition and TurboFan. It quickly compiles bytecode to machine code without much optimization, providing a significant speedup over the interpreter for code that is executed more than a few times.',
    'SPv2': 'Slimming Paint v2. The former name for the Composite After Paint (CAP) project.',
    'Surface': 'In Viz, a destination for a client to submit CompositorFrames. Identified by a SurfaceId.',
    'SVG': 'Scalable Vector Graphics.',
    'Tab': 'The UI element representing a single webpage or WebUI page within a browser window.',
    'ToT': 'Tip of Tree - The most recent revision in the source code repository.',
    'Torque': "A domain-specific language developed by the V8 team with a TypeScript-like syntax. It's used to write many of V8's builtins and parts of the runtime, offering higher-level abstractions and better type safety than writing raw CodeStubAssembler code.",
    'TSan': 'ThreadSanitizer - A tool for detecting data races.',
    'TurboFan': "V8's optimizing compiler. It takes bytecode and feedback from the interpreter (or lower-tier compilers) to generate highly optimized machine code for frequently executed functions (hot functions).",
    'UI Compositor': "Located in //ui/compositor, this component is used by the Browser process's "
    "native"
    " UI (Views) to produce composited content, which it sends to CC and then to Viz.",
    'UMA': "User Metrics Analysis - Chromium's system for collecting anonymized usage data to improve the browser.",
    'V8': "Google's open-source high-performance JavaScript engine used in Chrome.",
    'Sandbox': 'In V8, a security feature that attempts to confine all V8 heap objects within a reserved virtual memory region. Pointers within the sandbox are often compressed or use indirections through tables outside the sandbox, mitigating heap corruption vulnerabilities. (V8 Sandbox - Glossary)',
    'Views': 'A cross-platform UI toolkit within Chromium for building native-looking user interfaces (buttons, dialogs, windows, etc.) from C++. It\'s used for most of the browser chrome on Desktop platforms and Ash.',
    'Viz': "Short for Visuals - The component responsible for compositing and rendering in Chrome.",
    'Vulkan': 'A modern cross-platform graphics and compute API.',
    'WAI': 'Working As Intended - Indicates that a reported behavior is not a bug but the expected behavior.',
    'Wasm': 'V8 includes a compiler and runtime for WebAssembly, a low-level binary format for the web.',
    'WebGL': 'Web Graphics Library. A JavaScript API for rendering interactive 2D and 3D graphics, based on OpenGL ES.',
    'WebGPU': 'A modern API providing low-level graphics and compute capabilities on the Web, a successor to WebGL.',
    'WebContents': 'A core class in the Content Module representing the contents of a tab. It owns the RenderViewHost and manages the lifecycle of a rendered page.',
    'WebUI': 'A framework allowing parts of the browser UI to be built using web technologies (HTML, CSS, JavaScript). Examples include the Settings page (chrome://settings), History (chrome://history), and New Tab Page. These pages run in a renderer process but have special bindings to communicate with the browser process.',
    'Widget': "In the Views framework, a Widget is a top-level window on the screen that hosts a hierarchy of View objects. It bridges the gap between the platform's native windowing system and the Views toolkit.",
  }

  # Dynamically build the glossary string
  glossary_str = ""
  for term, definition in glossary.items():
    if any(term.lower() in msg.lower() for msg in messages):
      glossary_str += f'{term}: {definition}\n'

  prompt = (
    "You are an expert software engineering analyst. Based on the following change descriptions, "
    "your task is to generate a concise title and a detailed summary for the topic they represent. "
    "The audience for the summary is a software engineer who will use this information to diagnose or implement features in the future. "
    "The summary should detail the key changes, files, and classes involved, using clear sections and markdown for clarity. "
    "Include the year ranges for areas of work (year is the right coarseness). Include key bug ID references for fixes and reverts made in the last 90 days from the current date. Clearly label bugs with 'BUG:<bugid>'. "
    "Include details on any class or feature removals and deprecations mentioned in the history. "
    "Do not include general non-specific advice. Assume the reader is an expert.\n\n"
    "Your response MUST follow this format:\n"
    "Line 1: A single, concise, title-case phrase (fewer than 12 words).\n"
    "Line 2: A blank line.\n"
    "Line 3 onwards: The detailed summary, formatted in markdown.\n\n"
    "Example:\n"
    "Refactor UI Composition and Layer Handling\n\n"
    "### Overview\nThis topic describes the extensive evolution and refinement of surface management...\n\nKey Concepts and Evolution\n* Surface Identifiers: ...\n\n###Key Changes by Area\n1. Foundational Lifecycle and Identification (2013-2016)\n ...\n\n"
  )

  if glossary_str:
    prompt += f"Use this glossary of terms you may encounter:\n<glossary>\n{glossary_str}</glossary>\n\n"

  changes_str = "\n".join(f"<change>{message}</change>" for message in messages)

  prompt += (
    f"Changes in chronological order:\n<changes>\n{changes_str}\n</changes>\n"
  )
  return prompt


def get_title_prompt(summary: str) -> str:
  """
  Generates the prompt for creating a concise title from a summary.
  """
  prompt = (
    "You are an expert technical writer. Based on the following detailed summary, "
    "generate a single, concise, title-case phrase (fewer than 12 words) that captures the main topic. "
    "This will be used as a hyperlink title. Do not include quotes or special characters."
    "\nSummary:\n" + summary
  )
  return prompt
