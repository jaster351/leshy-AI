package ru.leshy.satellite

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Small, dependency-free design system for the Android companion.
 * The conversational screen is intentionally first; diagnostics and sensitive
 * device settings belong inside the expandable settings panel.
 */
internal class LeshyMobileUi(private val context: Context) {
    val backgroundColor: Int = Color.rgb(8, 16, 25)
    val foregroundColor: Int = Color.rgb(238, 248, 244)
    val mutedColor: Int = Color.rgb(155, 175, 174)
    val accentColor: Int = Color.rgb(87, 231, 174)
    val panelColor: Int = Color.rgb(18, 33, 43)

    fun dp(value: Int): Int = (context.resources.displayMetrics.density * value + 0.5f).toInt()

    fun column(padding: Int = 0): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        if (padding > 0) setPadding(dp(padding), dp(padding), dp(padding), dp(padding))
    }

    fun panel(prominent: Boolean = false): LinearLayout = column(20).apply {
        background = GradientDrawable(
            GradientDrawable.Orientation.TL_BR,
            if (prominent) intArrayOf(Color.rgb(21, 65, 68), Color.rgb(16, 35, 45))
            else intArrayOf(panelColor, Color.rgb(15, 29, 39))
        ).apply {
            cornerRadius = dp(24).toFloat()
            setStroke(dp(1), if (prominent) Color.rgb(53, 111, 100) else Color.rgb(38, 57, 65))
        }
        elevation = dp(3).toFloat()
    }

    fun label(value: String, size: Float = 14f, muted: Boolean = false, bold: Boolean = false): TextView =
        TextView(context).apply {
            text = value
            textSize = size
            setTextColor(if (muted) mutedColor else foregroundColor)
            if (bold) typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
            setLineSpacing(dp(2).toFloat(), 1f)
        }

    fun button(label: String, prominent: Boolean = false): Button = Button(context).apply {
        text = label
        isAllCaps = false
        textSize = if (prominent) 17f else 14f
        setTextColor(if (prominent) backgroundColor else foregroundColor)
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        gravity = Gravity.CENTER
        minHeight = dp(if (prominent) 58 else 48)
        minimumHeight = dp(if (prominent) 58 else 48)
        backgroundTintList = null
        background = GradientDrawable(
            GradientDrawable.Orientation.LEFT_RIGHT,
            if (prominent) intArrayOf(Color.rgb(104, 244, 181), Color.rgb(60, 206, 184))
            else intArrayOf(Color.rgb(35, 57, 65), Color.rgb(29, 49, 60))
        ).apply {
            cornerRadius = dp(16).toFloat()
            setStroke(dp(1), if (prominent) Color.rgb(105, 241, 190) else Color.rgb(55, 82, 89))
        }
    }

    fun input(hintText: String): EditText = EditText(context).apply {
        hint = hintText
        textSize = 15f
        setTextColor(foregroundColor)
        setHintTextColor(mutedColor)
        setPadding(dp(14), dp(14), dp(14), dp(14))
        backgroundTintList = null
        background = GradientDrawable().apply {
            setColor(Color.rgb(10, 26, 35))
            cornerRadius = dp(14).toFloat()
            setStroke(dp(1), Color.rgb(50, 76, 81))
        }
    }

    fun add(parent: LinearLayout, item: View, top: Int = 0, bottom: Int = 0) {
        parent.addView(item, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply {
            topMargin = dp(top)
            bottomMargin = dp(bottom)
        })
    }
}
