package ru.leshy.satellite

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject

/** Local-only 3D presentation. The existing authenticated voice session remains the source of truth. */
@SuppressLint("SetJavaScriptEnabled")
internal class HologramView(
    context: Context,
    private val onClose: () -> Unit,
    private val onTalk: () -> Unit
) : FrameLayout(context) {
    private val handler = Handler(Looper.getMainLooper())
    private val prefs = context.getSharedPreferences("leshy_hologram", Context.MODE_PRIVATE)
    private var mode = prefs.getInt("mode", 1).let { if (it == 4) 4 else 1 }
    private var reflected = prefs.getBoolean("reflected", true)
    private var ready = false
    private var disposed = false
    private var active = false
    private var phase = "idle"
    private var gaze = 0f
    private var viseme = 0.55f
    private val hide = Runnable { controls.visibility = View.GONE }
    private val browser = WebView(context)
    private val controls = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        setBackgroundColor(0xE5122017.toInt())
        setPadding(12, 8, 12, 8)
    }
    private val status = TextView(context).apply {
        setTextColor(Color.WHITE)
        textSize = 14f
    }
    private val talk = Button(context)
    private val optical = Button(context)
    private val mirror = Button(context)

    init {
        setBackgroundColor(Color.BLACK)
        browser.setBackgroundColor(Color.BLACK)
        browser.settings.apply {
            javaScriptEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            blockNetworkLoads = true
            javaScriptCanOpenWindowsAutomatically = false
        }
        browser.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean = true
            override fun onPageFinished(view: WebView?, url: String?) {
                if (disposed || url != "file:///android_asset/leshy_hologram.html") return
                ready = true
                sync()
            }
        }
        addView(browser, LayoutParams(-1, -1))
        browser.setOnTouchListener { _, event ->
            if (event.actionMasked == MotionEvent.ACTION_UP) {
                if (controls.visibility == View.VISIBLE) {
                    handler.removeCallbacks(hide)
                    controls.visibility = View.GONE
                } else reveal()
            }
            false
        }
        controls.addView(status)
        val row = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
        fun addButton(button: Button) {
            button.isAllCaps = false
            row.addView(button, LinearLayout.LayoutParams(0, -2, 1f))
        }
        talk.setOnClickListener { onTalk(); reveal() }
        optical.setOnClickListener {
            mode = if (mode == 1) 4 else 1
            save(); sync(); updateLabels(); reveal()
        }
        mirror.setOnClickListener {
            reflected = !reflected
            save(); sync(); updateLabels(); reveal()
        }
        addButton(talk)
        addButton(optical)
        addButton(mirror)
        controls.addView(row)
        controls.addView(Button(context).apply {
            text = "Выйти из голограммы"
            isAllCaps = false
            setOnClickListener { onClose() }
        })
        addView(controls, LayoutParams(-1, -2, Gravity.BOTTOM))
        updateLabels()
        browser.loadUrl("file:///android_asset/leshy_hologram.html")
    }

    private fun save() {
        prefs.edit().putInt("mode", mode).putBoolean("reflected", reflected).apply()
    }

    private fun js(source: String) {
        if (ready && !disposed) browser.evaluateJavascript(source, null)
    }

    private fun sync() {
        js("window.leshyMode($mode,$reflected);window.leshyState(" +
            JSONObject.quote(phase) + ");window.leshyLook($gaze);window.leshyPause(${!active});")
        if (phase == "speaking") js("window.leshyViseme($viseme)")
    }

    fun setPhase(value: String) {
        if (disposed || value == phase) return
        phase = value
        js("window.leshyState(" + JSONObject.quote(value) + ")")
        updateLabels()
    }

    fun setStatus(message: String, conversation: Boolean) {
        status.text = message
        talk.text = if (conversation) "Завершить" else "Говорить"
    }

    fun setGaze(value: Float) {
        if (disposed) return
        gaze = value.coerceIn(-0.6f, 0.6f)
        js("window.leshyLook($gaze)")
    }

    fun setViseme(value: Float) {
        if (disposed) return
        viseme = value.coerceIn(0f, 1f)
        js("window.leshyViseme($viseme)")
    }

    private fun updateLabels() {
        optical.text = if (mode == 1) "1 зеркало" else "4 грани"
        mirror.text = if (reflected) "Отражение: да" else "Отражение: нет"
        if (talk.text.isNullOrEmpty()) talk.text = "Говорить"
        if (status.text.isNullOrEmpty()) status.text = "Леший • голографический режим"
    }

    private fun reveal() {
        if (!active || disposed) return
        controls.visibility = View.VISIBLE
        handler.removeCallbacks(hide)
        handler.postDelayed(hide, 6000)
    }

    fun setActive(value: Boolean) {
        if (disposed) return
        active = value
        if (active) {
            browser.onResume()
            sync()
            reveal()
        } else {
            handler.removeCallbacks(hide)
            js("window.leshyPause(true)")
            browser.onPause()
        }
    }

    fun dispose() {
        if (disposed) return
        setActive(false)
        disposed = true
        removeView(browser)
        browser.destroy()
    }
}
