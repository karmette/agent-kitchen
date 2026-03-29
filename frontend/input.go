package main

import (
	"strconv"

	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"
	lg "charm.land/lipgloss/v2"
)

var (
	inputContainer = lg.NewStyle().
			Border(lg.RoundedBorder()).
			BorderForeground(lg.Color("#7D56F4")).
			Padding(1, 2)

	instructionStyle = lg.NewStyle().Foreground(lg.Color("#626262"))
)

type InputModel struct {
	textInput    textinput.Model
	iterationsTi textinput.Model
	cellsTi      textinput.Model
	done         bool
	focusedField int
}

func NewInputModel(iterations, cells int) InputModel {
	ti := textinput.New()
	ti.Placeholder = "Enter a prompt..."
	ti.Focus()
	ti.SetWidth(40)

	iterTi := textinput.New()
	iterTi.Placeholder = "Iterations"
	iterTi.SetWidth(40)

	cellsTi := textinput.New()
	cellsTi.Placeholder = "Cells"
	cellsTi.SetWidth(40)

	return InputModel{
		textInput:    ti,
		iterationsTi: iterTi,
		cellsTi:      cellsTi,
		done:         false,
		focusedField: 0,
	}
}

func (m *InputModel) SetWidth(w int) {
	m.textInput.SetWidth(40)
	m.iterationsTi.SetWidth(40)
	m.cellsTi.SetWidth(40)
}

func (m InputModel) Init() tea.Cmd {
	return textinput.Blink
}

func (m InputModel) Update(msg tea.Msg) (InputModel, tea.Cmd) {
	var cmd tea.Cmd
	handled := false

	switch msg := msg.(type) {
	case tea.KeyPressMsg:
		switch msg.String() {
		case "ctrl+c", "esc":
			return m, tea.Quit
		case "enter":
			m.done = true
			return m, nil
		case "up":
			if m.focusedField > 0 {
				m.focusedField--
				m.updateFocus()
			}
			handled = true
		case "down":
			if m.focusedField < 2 {
				m.focusedField++
				m.updateFocus()
			}
			handled = true
		}
	}

	if !handled {
		switch m.focusedField {
		case 0:
			m.textInput, cmd = m.textInput.Update(msg)
		case 1:
			m.iterationsTi, cmd = m.iterationsTi.Update(msg)
		case 2:
			m.cellsTi, cmd = m.cellsTi.Update(msg)
		}
	}
	return m, cmd
}

func (m *InputModel) updateFocus() {
	switch m.focusedField {
	case 0:
		m.textInput.Focus()
		m.iterationsTi.Blur()
		m.cellsTi.Blur()
	case 1:
		m.textInput.Blur()
		m.iterationsTi.Focus()
		m.cellsTi.Blur()
	case 2:
		m.textInput.Blur()
		m.iterationsTi.Blur()
		m.cellsTi.Focus()
	}
}

func (m InputModel) View() string {
	s := lg.JoinVertical(lg.Left,
		lg.NewStyle().Bold(true).Render("Prompt Terminal"),
		"\n",
		m.textInput.View(),
		"\n",
		lg.NewStyle().Foreground(lg.Color("#888888")).Render("Options:"),
		m.iterationsTi.View(),
		m.cellsTi.View(),
		"\n",
		instructionStyle.Render("press enter to run • up/down to navigate • esc to quit"),
	)
	return inputContainer.Render(s)
}

func (m InputModel) GetIterations() int {
	val, err := strconv.Atoi(m.iterationsTi.Value())
	if err != nil || val < 1 {
		return 10
	}
	return val
}

func (m InputModel) GetCells() int {
	val, err := strconv.Atoi(m.cellsTi.Value())
	if err != nil || val < 1 {
		return 25
	}
	return val
}
