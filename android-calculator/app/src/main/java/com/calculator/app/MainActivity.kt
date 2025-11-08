package com.calculator.app

import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import java.text.DecimalFormat

class MainActivity : AppCompatActivity() {

    private lateinit var display: TextView
    private var currentNumber = ""
    private var operator = ""
    private var firstNumber = 0.0
    private var isNewOperation = true
    private val decimalFormat = DecimalFormat("#.##########")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        display = findViewById(R.id.tvDisplay)

        // Number buttons
        val numberButtons = listOf(
            R.id.btn0, R.id.btn1, R.id.btn2, R.id.btn3, R.id.btn4,
            R.id.btn5, R.id.btn6, R.id.btn7, R.id.btn8, R.id.btn9
        )

        numberButtons.forEach { id ->
            findViewById<MaterialButton>(id).setOnClickListener { onNumberClick(it) }
        }

        // Operator buttons
        findViewById<MaterialButton>(R.id.btnAdd).setOnClickListener { onOperatorClick("+") }
        findViewById<MaterialButton>(R.id.btnSubtract).setOnClickListener { onOperatorClick("-") }
        findViewById<MaterialButton>(R.id.btnMultiply).setOnClickListener { onOperatorClick("×") }
        findViewById<MaterialButton>(R.id.btnDivide).setOnClickListener { onOperatorClick("÷") }
        findViewById<MaterialButton>(R.id.btnPercent).setOnClickListener { onPercentClick() }

        // Special buttons
        findViewById<MaterialButton>(R.id.btnEquals).setOnClickListener { onEqualsClick() }
        findViewById<MaterialButton>(R.id.btnClear).setOnClickListener { onClearClick() }
        findViewById<MaterialButton>(R.id.btnDelete).setOnClickListener { onDeleteClick() }
        findViewById<MaterialButton>(R.id.btnDecimal).setOnClickListener { onDecimalClick() }
    }

    private fun onNumberClick(view: View) {
        val button = view as MaterialButton
        val number = button.text.toString()

        if (isNewOperation) {
            currentNumber = ""
            isNewOperation = false
        }

        currentNumber += number
        updateDisplay(currentNumber)
    }

    private fun onOperatorClick(op: String) {
        if (currentNumber.isEmpty() && operator.isEmpty()) {
            return
        }

        if (operator.isNotEmpty() && !isNewOperation) {
            onEqualsClick()
        }

        operator = op
        firstNumber = currentNumber.toDoubleOrNull() ?: firstNumber
        isNewOperation = true
    }

    private fun onEqualsClick() {
        if (operator.isEmpty() || currentNumber.isEmpty()) {
            return
        }

        val secondNumber = currentNumber.toDoubleOrNull() ?: 0.0
        val result = when (operator) {
            "+" -> firstNumber + secondNumber
            "-" -> firstNumber - secondNumber
            "×" -> firstNumber * secondNumber
            "÷" -> {
                if (secondNumber == 0.0) {
                    updateDisplay("Error")
                    reset()
                    return
                }
                firstNumber / secondNumber
            }
            else -> secondNumber
        }

        currentNumber = formatResult(result)
        updateDisplay(currentNumber)
        operator = ""
        isNewOperation = true
    }

    private fun onPercentClick() {
        if (currentNumber.isEmpty()) {
            return
        }

        val number = currentNumber.toDoubleOrNull() ?: 0.0
        val result = number / 100
        currentNumber = formatResult(result)
        updateDisplay(currentNumber)
        isNewOperation = true
    }

    private fun onClearClick() {
        reset()
        updateDisplay("0")
    }

    private fun onDeleteClick() {
        if (currentNumber.isNotEmpty() && !isNewOperation) {
            currentNumber = currentNumber.dropLast(1)
            if (currentNumber.isEmpty()) {
                updateDisplay("0")
            } else {
                updateDisplay(currentNumber)
            }
        }
    }

    private fun onDecimalClick() {
        if (isNewOperation) {
            currentNumber = "0."
            isNewOperation = false
        } else if (!currentNumber.contains(".")) {
            currentNumber += "."
        }
        updateDisplay(currentNumber)
    }

    private fun updateDisplay(text: String) {
        display.text = text
    }

    private fun formatResult(result: Double): String {
        return if (result == result.toLong().toDouble()) {
            result.toLong().toString()
        } else {
            decimalFormat.format(result)
        }
    }

    private fun reset() {
        currentNumber = ""
        operator = ""
        firstNumber = 0.0
        isNewOperation = true
    }
}
