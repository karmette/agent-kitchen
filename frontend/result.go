package main

import (
	"fmt"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	lg "charm.land/lipgloss/v2"
)

type tickMsg time.Time

var (
	resultContainer = lg.NewStyle().
			Border(lg.DoubleBorder()).
			BorderForeground(lg.Color("#3993ED")).
			Padding(1, 2)

	cellStyle = lg.NewStyle().
			Border(lg.NormalBorder()).
			BorderForeground(lg.Color("#CCCCCC"))

	doneCellStyle = lg.NewStyle().
			Border(lg.NormalBorder()).
			BorderForeground(lg.Color("#81F88F"))
	focusedCellStyle = lg.NewStyle().
				Border(lg.NormalBorder()).
				BorderForeground(lg.Color("#09588A"))

	sidePanelStyle = lg.NewStyle().
			Border(lg.RoundedBorder()).
			BorderForeground(lg.Color("#96BEFD")).
			PaddingLeft(2)

	listItemStyle = lg.NewStyle().
			Foreground(lg.Color("#FAFAFA")).
			PaddingLeft(2)

	focusedListItemStyle = lg.NewStyle().
				UnderlineColor(lg.Color("#96BEFD")).
				PaddingLeft(2).
				UnderlineStyle(lg.UnderlineSingle)
)

type CellStatus string

const (
	StatusPending CellStatus = "pending"
	StatusRunning CellStatus = "running"
	StatusDone    CellStatus = "done"
	StatusError   CellStatus = "error"
)

type cell struct {
	status  CellStatus
	conv    conversation
	content strings.Builder
}
type ViewMode int

const (
	ModeGrid ViewMode = iota
	ModeDetail
)

type ResultModel struct {
	content    string
	rows       int
	cols       int
	iterations int
	focusedRow int
	focusedCol int
	width      int
	height     int
	cells      []cell
	mode       ViewMode // NEW: Track the current view mode
}

func NewResultModel(rows, cols, iterations int) *ResultModel {
	totalCells := rows * cols

	cells := make([]cell, totalCells)

	for i := range cells {
		cells[i].status = StatusPending
		cells[i].conv = newConversation()
		go cells[i].conv.start()
	}
	return &ResultModel{
		content:    "",
		rows:       rows,
		cols:       cols,
		iterations: iterations,
		focusedRow: 0,
		focusedCol: 0,
		width:      0,
		height:     0,
		cells:      cells,
		mode:       ModeGrid,
	}
}

func (m *ResultModel) Init() tea.Cmd {
	return tea.Tick(time.Millisecond*100, func(t time.Time) tea.Msg {
		return tickMsg(t)
	})
}

func (m *ResultModel) Update(msg tea.Msg) tea.Cmd {
	m.checkChannels()

	switch msg := msg.(type) {
	case tickMsg:
		return tea.Tick(time.Millisecond*100, func(t time.Time) tea.Msg {
			return tickMsg(t)
		})
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
	case tea.KeyMsg:
		switch msg.String() {
		case "q", "ctrl+c":
			return tea.Quit
			
		// NEW: Mode switching keys
		case "enter", "o":
			if m.mode == ModeGrid {
				m.mode = ModeDetail
			}
		case "esc":
			if m.mode == ModeDetail {
				m.mode = ModeGrid
			}

		// Wrap navigation in a check to ensure we only move in Grid mode
		case "h", "left":
			if m.mode == ModeGrid {
				if m.focusedCol > 0 {
					m.focusedCol--
				} else {
					m.focusedCol = m.cols - 1
					if m.focusedRow > 0 {
						m.focusedRow--
					} else {
						m.focusedRow = m.rows - 1
					}
				}
			}
		case "l", "right":
			if m.mode == ModeGrid {
				if m.focusedCol < m.cols-1 {
					m.focusedCol++
				} else {
					m.focusedCol = 0
					if m.focusedRow < m.rows-1 {
						m.focusedRow++
					} else {
						m.focusedRow = 0
					}
				}
			}
		case "k", "up":
			if m.mode == ModeGrid {
				if m.focusedRow > 0 {
					m.focusedRow--
				} else {
					m.focusedRow = m.rows - 1
				}
			}
		case "j", "down":
			if m.mode == ModeGrid {
				if m.focusedRow < m.rows-1 {
					m.focusedRow++
				} else {
					m.focusedRow = 0
				}
			}
		}
	}
	return nil
}

func (m *ResultModel) checkChannels() {
	for i := range m.cells {
		if m.cells[i].status == StatusDone {
			continue
		}

		select {
		case msg := <-m.cells[i].conv.ch:
			m.cells[i].content.WriteString(msg)
		case <-m.cells[i].conv.done:
			m.cells[i].status = StatusDone
		default:
		}
	}
}

func (m *ResultModel) getLastLines(content string, maxLines int) string {
	lines := strings.Split(content, "\n")
	if len(lines) <= maxLines {
		return content
	}
	return strings.Join(lines[len(lines)-maxLines:], "\n")
}

func (m *ResultModel) View() string {
	if m.width == 0 || m.height == 0 {
		return resultContainer.Render("Initializing...")
	}

	sidePanelWidth := 30

	innerWidth := m.width - resultContainer.GetHorizontalFrameSize()
	innerHeight := m.height - resultContainer.GetVerticalFrameSize()
	// NEW: Detail View Rendering
	if m.mode == ModeDetail {
		idx := m.focusedRow*m.cols + m.focusedCol
		
		// Create a header for the detailed view
		header := lg.NewStyle().
			Foreground(lg.Color("#397fdb")).
			Bold(true).
			Render(fmt.Sprintf("--- Cell %d Chat (Press ESC to return) ---\n\n", idx+1))

		// Get the chat content. We subtract a few lines from innerHeight to account for the header.
		chatContent := m.getLastLines(m.cells[idx].content.String(), innerHeight-4)
		
		// Apply a style that takes up the full available width and height
		detailView := lg.NewStyle().
			Width(innerWidth).
			Height(innerHeight).
			Render(header + chatContent)

		return resultContainer.Render(detailView)
	}

	gridWidth := innerWidth - sidePanelWidth - 1
	cellWidth := gridWidth / m.cols
	cellHeight := innerHeight / m.rows

	var gridRows []string
	for r := 0; r < m.rows; r++ {
		var cells []string
		for c := 0; c < m.cols; c++ {
			var cell lg.Style
			if r == m.focusedRow && c == m.focusedCol {
				cell = focusedCellStyle
			} else {
				cell = cellStyle
			}
			idx := r*m.cols + c
			cell = cell.Width(cellWidth).Height(cellHeight)
			// Get the last lines
			cellContent := m.getLastLines(m.cells[idx].content.String(), cellHeight-2)

			// NEW: Truncate lines that are too long
			maxLineWidth := cellWidth - 5
			cellContent = m.truncateLines(cellContent, maxLineWidth)
			cells = append(cells, cell.Render(cellContent))
		}
		gridRows = append(gridRows, lg.JoinHorizontal(lg.Top, cells...))
	}
	gridContent := lg.JoinVertical(lg.Top, gridRows...)

	var listItems []string
	for i := 0; i < m.rows*m.cols; i++ {
		row := i / m.cols
		col := i % m.cols
		cellNum := i + 1
		status := m.cells[i].status
		item := fmt.Sprintf("Cell %d: %s", cellNum, status)

		var style lg.Style
		if row == m.focusedRow && col == m.focusedCol {
			style = focusedListItemStyle
		} else {
			style = listItemStyle
		}
		listItems = append(listItems, style.Render(item))
	}
	sideContent := lg.JoinVertical(lg.Top, listItems...)

	content := lg.JoinHorizontal(lg.Top,
		gridContent,
		sidePanelStyle.Width(sidePanelWidth).Height(innerHeight).Render(sideContent),
	)
	return resultContainer.Render(content)
}
func (m *ResultModel) truncateLines(content string, maxWidth int) string {
	if maxWidth <= 0 {
		return ""
	}

	lines := strings.Split(content, "\n")
	for i, line := range lines {
		runes := []rune(line)
		if len(runes) > maxWidth {
			// Truncate and append ...
			lines[i] = string(runes[:maxWidth]) + "..."
		}
	}

	return strings.Join(lines, "\n")
}
